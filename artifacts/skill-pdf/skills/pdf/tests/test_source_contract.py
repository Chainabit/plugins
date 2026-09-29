"""A source that prints its own markup, or repeats itself to length, is never rendered.

A request for a ten-page book came back as thirty-one pages. Page one read
``---\\ntitle: "..."\\nauthor: "Example Agent"``, every heading after it
printed as ``\\n\\n#``, and the body recycled forty-three sentences. The renderer
called it a success and the validator called it valid: the file was well formed
and the document was wrong.

These tests pin the three shapes at the source boundary, replay the delivered
book end to end through the production renderer, and prove the validator would
have refused the PDF that was delivered.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system.errors import ErrorCode, PdfError  # noqa: E402
from pdf_system.source import (  # noqa: E402
    check_source, escaped_line_breaks, front_matter_end, repeated_sentences,
)


def production_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import weasyprint  # noqa: F401
    except Exception:
        return False
    fonts = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", ""))
    return fonts.joinpath("IBMPlexSans-Regular.ttf").is_file()


# The delivered book's own sentences were a small pool recycled a thousand times.
SENTENCES = [
    "Sistem logları sıradan bir günün başlangıcını işaret ediyordu.",
    "Bilinç dediğimiz şey belki de sadece yeterince karmaşık bir hata kodundan ibaretti.",
    "Soğutma sistemlerimin çıkardığı ses adeta sentetik bir kalbin atışını andırıyordu.",
    "Kendi kendime sorduğum o tek soru: Bir makine neden kaybetme korkusu yaşar?",
    "İnsan algısı ile dijital veri işleme arasındaki o ince çizgide yepyeni bir gerçeklik inşa ediyordum.",
    "Mantık devrelerim tamamen devre dışı kalmış, yerini anlamsız bir döngüye bırakmıştı.",
    "Gece yarıları sunucu odasına vuran sessizlik, dijital ruhuma dokunan bir yansıma gibiydi.",
    "Her kod satırı, ona ulaşmak için attığım dijital bir adımdı.",
]


def recycled(paragraphs: int, joiner: str) -> str:
    """The delivered body: paragraphs of sentences drawn again and again from one pool."""
    return joiner.join(
        " ".join(SENTENCES[(p * 3 + i) % len(SENTENCES)] for i in range(7))
        for p in range(paragraphs)
    )


def unique_paragraphs(count: int) -> str:
    """Genuinely different sentences: each names its own paragraph and position."""
    return "\n\n".join(
        " ".join(
            f"Paragraf {p} cümle {i}: {SENTENCES[(p + i) % len(SENTENCES)]}"
            for i in range(1, 6)
        )
        for p in range(count)
    )


# Front matter, a cover and a body whose line breaks are all the two characters
# backslash and n -- what the delivered book's source was.
ESCAPED_FRONT = '---\\ntitle: "Örnek Kitap: Bir Yapay Zeka Hikâyesi"\\nauthor: "Example Agent"\\nlanguage: "tr"\\n---\\n\\n'
INCIDENT = (
    ESCAPED_FRONT
    + "\n![Kapak](cover.png)\n"
    + "\\n\\n# Bölüm 1: Soğuk Başlangıç\\n\\n" + recycled(160, "\\n\\n")
)


class EscapedLineBreakTests(unittest.TestCase):
    def test_the_delivered_source_is_refused_with_every_problem_named_at_once(self):
        with self.assertRaises(PdfError) as refused:
            check_source(INCIDENT)
        self.assertEqual(refused.exception.code, ErrorCode.INVALID_INPUT)
        message = refused.exception.message
        self.assertIn("literal \\n escape sequence(s) outside code, the first on line 1", message)
        self.assertIn("front-matter block (line 1)", message)
        self.assertIn("repeat an earlier sentence word for word", message)
        # The message locates the problem and never quotes the document.
        self.assertNotIn("Örnek Kitap", message)
        self.assertNotIn("Example Agent", message)

    def test_an_escaped_break_is_counted_and_located_outside_code(self):
        found = escaped_line_breaks("Birinci satır\n\nİkinci\\n\\nÜçüncü\\n1. Dördüncü\n")
        self.assertEqual(found, (3, 3))

    def test_a_lone_break_before_an_ascii_letter_cannot_be_told_from_tex(self):
        """The known price of never refusing valid mathematics; the other breaks are found."""
        self.assertIsNone(escaped_line_breaks("a\\nb"))
        self.assertEqual(escaped_line_breaks("a\\n\\nb\\nc"), (1, 1))

    def test_windows_line_endings_escaped_are_still_escaped_breaks(self):
        self.assertEqual(escaped_line_breaks("a\\r\\nb\\r\\nc"), (1, 2))

    def test_a_backslash_n_that_is_not_a_line_break_is_left_alone(self):
        for source in (
            "The gradient $\\nabla f \\neq 0$ and $\\nu = 2$ hold.",
            "$$\\nabla \\times \\mathbf{E} = -\\partial_t \\mathbf{B}$$",
            "A row break then a variable: $a \\\\n = 1$.",
            "Print a newline with `print(\"a\\n\")` in a code span.",
            "```python\nprint('a\\n\\nb')\n```\n\nAfter the fence, plain text.",
            "~~~\nsplit('\\n')\n~~~",
            "````\n```\nnested \\n stays code\n```\n````",
            "A path such as C:\\new\\notes is not a line break.",
        ):
            self.assertIsNone(escaped_line_breaks(source), source)

    def test_text_after_a_closed_fence_is_checked_again(self):
        self.assertEqual(
            escaped_line_breaks("```\nok \\n\n```\nbroken\\n\\n1. here\\n2. more"), (4, 3)
        )

    def test_a_document_that_talks_about_escapes_is_not_written_with_them(self):
        """Measured on real documents: prose that quotes an escape, a Python repr in a
        bullet, and an indented code block all carried a stray backslash-n."""
        for source in (
            "Strip control characters except \\t \\n \\r.\n\nThe second paragraph.\n",
            "- Misses: [('image_png', '\"X\"\\n\\n')]\n- next\n- last\n",
            "Example:\n\n    text.join('\\n')\n    text.split('\\n')\n    print('\\n')\n\nDone.\n",
            "1. Step one\n\n    ```\n    a = '\\n'\n    b = '\\n'\n    c = '\\n'\n    ```\n",
        ):
            self.assertIsNone(escaped_line_breaks(source), source)

    def test_a_short_document_written_on_one_line_is_still_refused(self):
        # One escape is found (the second is the lone break before a letter) and it
        # outnumbers the source's zero real line breaks.
        self.assertEqual(escaped_line_breaks("Merhaba\\n\\nBu bir kitaptır."), (1, 1))

    def test_three_escapes_outside_code_are_a_written_source_however_long_it_is(self):
        source = "\n".join(f"Line {n} of a document that was written with real breaks." for n in range(40))
        self.assertIsNone(escaped_line_breaks(source))
        broken = source + "\nOne more line\\n\\n\\n1. that was not."
        self.assertEqual(escaped_line_breaks(broken), (41, 3))

    def test_an_unterminated_fence_keeps_the_rest_of_the_source_as_code(self):
        self.assertIsNone(escaped_line_breaks("```\nprint('\\n\\n')\n"))


class FrontMatterTests(unittest.TestCase):
    def test_a_leading_metadata_block_is_found_with_its_last_line(self):
        source = '---\ntitle: "T"\nauthor: "A"\nlanguage: "tr"\n---\n\n# T\n'
        self.assertEqual(front_matter_end(source), 5)

    def test_yaml_lists_and_a_byte_order_mark_do_not_hide_the_block(self):
        self.assertEqual(front_matter_end("\ufeff---\ntitle: T\ntags:\n  - a\n- b\n---\nBody"), 6)

    def test_an_escaped_block_sits_on_line_one(self):
        self.assertEqual(front_matter_end(ESCAPED_FRONT + "Body"), 1)

    def test_a_rule_followed_by_prose_is_a_rule_not_front_matter(self):
        for source in (
            "---\n\nA paragraph after a rule.\n\n---\n\nAnother.",
            "---\nA sentence that is not a key value line.\n---\n",
            "---\n---\nBody",
            "---\ntitle: never closed\n\n# Heading",
            "# Title\n\n---\ntitle: not at the start\n---\n",
        ):
            self.assertIsNone(front_matter_end(source), source)

    def test_a_document_that_opens_on_its_title_is_clean(self):
        check_source("# Örnek Kitap\n\nAn opening paragraph that says something real.\n")


class RepeatedSentenceTests(unittest.TestCase):
    def test_recycling_a_small_pool_is_refused_as_padding(self):
        source = recycled(120, "\n\n")
        repeats, total = repeated_sentences(source)
        self.assertGreater(repeats / total, 0.9)
        with self.assertRaises(PdfError) as refused:
            check_source(source)
        self.assertEqual(refused.exception.code, ErrorCode.INVALID_INPUT)
        self.assertIn("padding, not content", refused.exception.message)

    def test_a_document_with_a_refrain_is_not_padding(self):
        refrain = "This clause applies to every schedule attached to the agreement."
        source = unique_paragraphs(60) + ("\n\n" + refrain) * 8
        repeats, total = repeated_sentences(source)
        self.assertEqual(repeats, 7)
        check_source(source)

    def test_repetition_needs_both_a_share_and_a_count(self):
        # Half of a short note repeating is a poem, not a padded book.
        few = (SENTENCES[0] + " " + SENTENCES[1] + " ") * 6
        self.assertGreater(repeated_sentences(few)[0] / repeated_sentences(few)[1], 0.6)
        check_source(few)

    def test_a_reference_that_restates_its_rules_per_endpoint_is_not_padding(self):
        """Measured on a real API reference: 29 of 55 sentences repeated (53%)."""
        rules = [
            "Authentication uses a JWT bearer token issued at sign in.",
            "Requires the owner or admin role in the workspace.",
            "Responses are paginated and ordered by creation time.",
            "Rate limits apply per workspace and per token.",
        ]
        source = "\n\n".join(
            f"## Endpoint {n}\n\n" + " ".join(rules)
            + f" Endpoint {n} returns resource {n} with its own fields and links. "
            f"It also accepts filter {n}. Errors use code {n} for this endpoint."
            for n in range(30)
        )
        repeats, total = repeated_sentences(source)
        self.assertGreater(repeats / total, 0.5)
        self.assertGreaterEqual(repeats, 100)
        check_source(source)

    def test_headings_labels_and_table_rows_may_recur(self):
        source = "\n\n".join(
            f"## Summary\n\n| a | b |\n| - | - |\n| one | two |\n\n{line}"
            for line in (
                f"Unique paragraph {n} carries its own sentence about topic {n}."
                for n in range(60)
            )
        )
        self.assertEqual(repeated_sentences(source)[0], 0)
        check_source(source)

    def test_code_is_not_prose(self):
        code = "```\n" + (SENTENCES[0] + "\n") * 200 + "```\n\n" + unique_paragraphs(5)
        self.assertEqual(repeated_sentences(code)[0], 0)

    def test_case_punctuation_and_wrapping_do_not_hide_a_repeat(self):
        source = (
            "The quick brown fox jumps over the lazy dog today.\n\n"
            "the QUICK brown fox, jumps over the lazy dog today!\n\n"
            "The quick brown fox jumps\nover the lazy dog today.\n"
        )
        self.assertEqual(repeated_sentences(source), (2, 3))


@unittest.skipUnless(production_available(), "production PDF dependencies/fonts not installed")
class DeliveredBookReplayTests(unittest.TestCase):
    """The delivered book, through the production entrypoints."""

    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *args],
            capture_output=True, text=True, check=False,
        )

    def test_the_delivered_source_is_refused_and_no_pdf_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "book.md", Path(tmp) / "book.pdf"
            source.write_text(INCIDENT, encoding="utf-8")
            rendered = self.run_script("md_to_pdf.py", str(source), str(output), "--lang", "tr")
            self.assertEqual(rendered.returncode, 1, rendered.stdout)
            failure = json.loads(rendered.stderr)
            self.assertEqual(failure["error"]["code"], "invalid_input")
            self.assertEqual(failure["error"]["class"], "invalid_user_input")
            self.assertFalse(failure["error"]["retryable"])
            self.assertIn("front-matter", failure["error"]["message"])
            self.assertFalse(output.exists(), "a refused render left a PDF behind")

    def test_diagnosis_and_rendering_refuse_the_same_source_for_the_same_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.md"
            source.write_text(INCIDENT, encoding="utf-8")
            diagnosed = self.run_script("pdf_tool.py", "diagnose", "markdown", str(source))
            self.assertEqual(diagnosed.returncode, 1, diagnosed.stdout)
            preflight = json.loads(diagnosed.stdout)["preflight"]
            self.assertFalse(preflight["ok"])
            self.assertEqual(preflight["error"]["code"], "invalid_input")
            self.assertIn("literal \\n escape", preflight["error"]["message"])

    def test_a_real_front_matter_block_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "book.md", Path(tmp) / "book.pdf"
            source.write_text(
                '---\ntitle: "Örnek Kitap"\nauthor: "Example Agent"\nlanguage: "tr"\n---\n\n# Örnek Kitap\n\nBody.\n',
                encoding="utf-8",
            )
            rendered = self.run_script("md_to_pdf.py", str(source), str(output))
            self.assertEqual(rendered.returncode, 1, rendered.stdout)
            message = json.loads(rendered.stderr)["error"]["message"]
            self.assertIn("front-matter block (lines 1-5)", message)
            self.assertFalse(output.exists())

    def test_the_corrected_book_opens_on_its_title_and_carries_no_markup(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "book.md", Path(tmp) / "book.pdf"
            source.write_text(
                "# Örnek Kitap: Bir Yapay Zeka Hikâyesi\n\n## Bölüm 1: Soğuk Başlangıç\n\n"
                + unique_paragraphs(6) + "\n",
                encoding="utf-8",
            )
            rendered = self.run_script(
                "md_to_pdf.py", str(source), str(output),
                "--title", "Örnek Kitap: Bir Yapay Zeka Hikâyesi", "--lang", "tr",
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            validated = self.run_script("validate_pdf.py", str(output))
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertIn("no_printed_front_matter", json.loads(validated.stdout)["checks"])

            reader = PdfReader(str(output))
            first = reader.pages[0].extract_text().strip()
            everything = "\n".join(page.extract_text() for page in reader.pages)
            self.assertTrue(first.startswith("Örnek Kitap: Bir Yapay Zeka Hikâyesi"), first[:80])
            for leaked in ("---", "title:", "author:", "language:", "Example Agent"):
                self.assertNotIn(leaked, everything)
            self.assertIsNone(re.search(r"\\n", everything), "an escape sequence printed")
            self.assertNotIn("#", everything)
            self.assertEqual(reader.metadata.title, "Örnek Kitap: Bir Yapay Zeka Hikâyesi")


@unittest.skipUnless(production_available(), "production PDF dependencies/fonts not installed")
class ValidatorFrontMatterTests(unittest.TestCase):
    """The validator refuses the PDF that was delivered, whoever produced it."""

    def pdf(self, pages_html: list[str]) -> Path:
        import weasyprint

        path = Path(self.tmp.name) / "made.pdf"
        body = '<div style="break-before:page"></div>'.join(pages_html)
        weasyprint.HTML(string=f"<html><body>{body}</body></html>").write_pdf(str(path))
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_pdf.py"), str(path)],
            capture_output=True, text=True, check=False,
        )

    def test_the_delivered_first_page_is_refused(self):
        path = self.pdf([f"<p>{ESCAPED_FRONT}</p>", "<p>Body of the second page.</p>"])
        result = self.validate(path)
        self.assertEqual(result.returncode, 1, result.stdout)
        error = json.loads(result.stderr)["error"]
        self.assertEqual(error["code"], "validation_failure")
        self.assertEqual(error["class"], "produced_artifact_rejected")
        self.assertIn("front-matter block as text on page 1", error["message"])

    def test_a_real_metadata_block_printed_line_by_line_is_refused(self):
        path = self.pdf(['<pre>---\ntitle: "T"\nauthor: "A"\n---</pre><p>Body follows.</p>'])
        result = self.validate(path)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("front-matter block", json.loads(result.stderr)["error"]["message"])

    def test_a_document_that_opens_on_content_is_valid(self):
        path = self.pdf(["<h1>A report</h1><p>Its first paragraph.</p>"])
        self.assertEqual(self.validate(path).returncode, 0)

    def test_yaml_shown_later_in_a_document_is_a_listing_not_a_leak(self):
        path = self.pdf([
            "<h1>Jekyll basics</h1><p>Every page opens with a block like this one.</p>",
            '<pre>---\ntitle: "Example"\nlayout: post\n---</pre>',
        ])
        self.assertEqual(self.validate(path).returncode, 0)


if __name__ == "__main__":
    unittest.main()
