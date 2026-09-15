"""Markdown reaches the PDF as structure, never as its markup.

The renderer used to convert a hand-picked subset of Markdown. Single-asterisk
emphasis, numbered lists, nested lists, block quotes and horizontal rules were
printed as their source characters, and a Markdown image was escaped into
base64 text that the validator then rejected. Sources are now read as
CommonMark with GFM tables. These tests pin that dialect at the HTML boundary
and, where the production renderer is installed, prove it on a real PDF by
extracting the PDF's text.
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as etree
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system import SecurityPolicy  # noqa: E402
from pdf_system.errors import ErrorCode, PdfError  # noqa: E402


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


def png_bytes(width: int = 160, height: int = 80) -> bytes:
    """A valid PNG built with the standard library."""
    rows = b"".join(
        b"\x00" + bytes((x * 3 + y) % 256 for x in range(width * 3)) for y in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


# Every construct the dialect covers, written the way a model writes it:
# two-space nesting under bullets, three-space nesting under numbers, and lists
# and tables that follow their lead-in line directly.
FIXTURE = """# Quarterly Release Readiness

*Prepared by the delivery team* on **2026-09-15**. Overall status: ***at risk***.

## Observed state

Open work, by priority:
- **P1** items
  - Android crash on resume *(Overdue: 2026-09-09)*
  - CDN contract renewal *(Overdue: 2026-09-13)*
    - Owner: _Infrastructure_
- **P2** items
  - Accessibility audit

## Recommended actions

1. Confirm the release scope
2. Close the P1 blockers
   1. Ship the crash fix
   2. Renew the CDN contract
3. Schedule the security review

---

> The launch date holds only if the **P1** items close this week.
>
> - Risk: the payment SDK review is unscheduled

Build from `release/1.4` and follow [the checklist](https://acme.example/checklist).

```bash
./build --release
```

Ownership:
| Item | Owner | Due |
|:-----|:-----:|----:|
| Crash fix | Mobile | 2026-09-09 |
| CDN renewal | Infra | 2026-09-13 |

![Burn-down chart](chart.png)

Closing remark.
"""


def own_text(item: etree.Element) -> str:
    """A list item's text without the text of its nested lists."""
    parts = [item.text or ""]
    for child in item:
        if child.tag not in ("ul", "ol"):
            parts.append("".join(child.itertext()))
        parts.append(child.tail or "")
    return " ".join("".join(parts).split())


class BlockAdapterTests(unittest.TestCase):
    """The CommonMark block adapter is text in, text out, so it runs without a parser."""

    def normalize(self, text: str) -> list[str]:
        from pdf_system.markdown_html import _BlockNormalizer

        return _BlockNormalizer().run(text.split("\n"))

    def test_container_indentation_becomes_four_columns_per_level(self):
        self.assertEqual(
            self.normalize("- a\n  - b\n    - c\n- d"), ["- a", "    - b", "        - c", "- d"]
        )
        self.assertEqual(self.normalize("1. one\n   - child\n2. two"), ["1. one", "    - child", "2. two"])
        self.assertEqual(self.normalize("1) a\n2) b"), ["1. a", "2. b"])

    def test_a_paragraph_is_ended_only_where_commonmark_ends_it(self):
        self.assertEqual(self.normalize("Intro:\n- a"), ["Intro:", "", "- a"])
        self.assertEqual(self.normalize("Steps\n2. b"), ["Steps", "2\\. b"])

    def test_nested_fences_and_quotes_keep_their_content(self):
        self.assertEqual(
            self.normalize("- item\n  ```\n  code\n  ```"), ["- item", "", "        code", ""]
        )
        self.assertEqual(self.normalize("> - a\n>   - b"), ["> - a", ">     - b", ""])
        with self.assertRaises(PdfError) as unclosed:
            self.normalize("```\ncode")
        self.assertEqual(unclosed.exception.code, ErrorCode.INVALID_INPUT)


@unittest.skipUnless(markdown_available(), "markdown is not installed")
class CommonMarkStructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.policy = SecurityPolicy(self.root, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def tree(self, text: str, policy: SecurityPolicy | None = None) -> etree.Element:
        from pdf_system.markdown_html import render_markdown

        return etree.fromstring(f"<body>{render_markdown(text, policy or self.policy)}</body>")

    def blocks(self, text: str) -> list[str]:
        return [child.tag for child in self.tree(text)]

    def outline(self, text: str) -> list[tuple[str, str]]:
        """Every list item as the path of list tags above it and its own text."""
        found: list[tuple[str, str]] = []

        def walk(element: etree.Element, path: tuple[str, ...]) -> None:
            for child in element:
                if child.tag in ("ul", "ol"):
                    for item in child:
                        found.append(("/".join(path + (child.tag,)), own_text(item)))
                        walk(item, path + (child.tag,))
                else:
                    walk(child, path)

        walk(self.tree(text), ())
        return found

    def test_lists_nest_by_their_content_column(self):
        self.assertEqual(
            self.outline("- a\n  - b\n    - c\n- d"),
            [("ul", "a"), ("ul/ul", "b"), ("ul/ul/ul", "c"), ("ul", "d")],
        )
        self.assertEqual(
            self.outline("1. one\n   - child\n2. two"),
            [("ol", "one"), ("ol/ul", "child"), ("ol", "two")],
        )
        self.assertEqual(self.outline("- a\n    - b"), [("ul", "a"), ("ul/ul", "b")])
        self.assertEqual(
            self.outline("1. a\n   1. b\n   2. c\n2. d"),
            [("ol", "a"), ("ol/ol", "b"), ("ol/ol", "c"), ("ol", "d")],
        )
        self.assertEqual(
            self.outline("1. a\n\n   - nested\n2. b"),
            [("ol", "a"), ("ol/ul", "nested"), ("ol", "b")],
        )

    def test_ordered_lists_keep_their_numbering(self):
        started = self.tree("3. c\n4. d").find("ol")
        self.assertEqual(started.get("start"), "3")
        self.assertEqual(len(started), 2)
        self.assertEqual(self.outline("1) a\n2) b"), [("ol", "a"), ("ol", "b")])
        self.assertEqual(self.blocks("- a\n1. b"), ["ul", "ol"])

    def test_blocks_interrupt_a_paragraph_as_in_commonmark(self):
        self.assertEqual(self.blocks("Intro:\n- a\n- b"), ["p", "ul"])
        self.assertEqual(self.blocks("Steps:\n1. a\n2. b"), ["p", "ol"])
        # Only a list starting at 1 may interrupt a paragraph.
        self.assertEqual(self.blocks("Steps\n2. b"), ["p"])
        table = self.tree("Lead\n| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertEqual([child.tag for child in table], ["p", "table"])
        self.assertEqual(len(table.findall("table/tbody/tr")), 1)
        self.assertEqual(self.blocks("- a\n## Heading"), ["ul", "h2"])
        self.assertEqual(self.blocks("- a\n---\nb"), ["ul", "hr", "p"])
        self.assertEqual(self.blocks("Title\n---"), ["h2"])
        self.assertEqual(self.blocks("#tag is text\n# Heading"), ["p", "h1"])

    def test_code_keeps_its_characters(self):
        fenced = self.tree("```bash\n./build --release *x*\n```")
        self.assertEqual(fenced.find("pre/code").text, "./build --release *x*\n")
        in_item = self.tree("- item\n  ```\n  code *x*\n  ```\n- next")
        self.assertEqual(in_item.find("ul/li/pre/code").text, "code *x*\n")
        self.assertEqual([item.find("p").text.strip() for item in in_item.find("ul")], ["item", "next"])
        self.assertEqual(self.tree("Use `co*de` here").find("p/code").text, "co*de")
        with self.assertRaises(PdfError) as unclosed:
            self.tree("```\nnever closed")
        self.assertEqual(unclosed.exception.code, ErrorCode.INVALID_INPUT)

    def test_block_quotes_hold_their_blocks(self):
        self.assertEqual(
            self.outline("> - a\n>   - b"),
            [("ul", "a"), ("ul/ul", "b")],
        )
        self.assertEqual(self.blocks("> - a\n>   - b"), ["blockquote"])
        lazy = self.tree("> quote line\nlazy continuation\n\nafter")
        self.assertEqual(" ".join(lazy.find("blockquote/p").text.split()), "quote line lazy continuation")

    def test_inline_markup_becomes_elements(self):
        paragraph = self.tree(
            "*(Overdue: 2026-09-09)* **bold** ***both*** _under_ ~~gone~~ snake_case_word"
        ).find("p")
        self.assertEqual(paragraph.find("em").text, "(Overdue: 2026-09-09)")
        self.assertEqual(paragraph.find("strong").text, "bold")
        self.assertEqual(paragraph.find("strong/em").text, "both")
        self.assertEqual(paragraph.findall("em")[1].text, "under")
        self.assertEqual(paragraph.find("del").text, "gone")
        text = "".join(paragraph.itertext())
        self.assertIn("snake_case_word", text)
        self.assertNotIn("*", text)
        self.assertNotIn("~", text)

    def test_links_keep_web_and_mail_targets_only(self):
        paragraph = self.tree(
            "[web](https://acme.example/a) [mail](mailto:team@acme.example) "
            "[relative](notes.md) [ftp](ftp://acme.example/x) <https://acme.example/b>"
        ).find("p")
        self.assertEqual(
            [link.get("href") for link in paragraph.iter("a")],
            ["https://acme.example/a", "mailto:team@acme.example", "https://acme.example/b"],
        )
        self.assertEqual([span.text for span in paragraph.iter("span")], ["relative", "ftp"])
        self.assertTrue(all(not span.attrib for span in paragraph.iter("span")))

    def test_raw_html_is_printed_as_text(self):
        paragraph = self.tree("x <b>bold</b> <span style='color:red'>s</span> y<br>z").find("p")
        self.assertEqual([child.tag for child in paragraph], ["br"])
        self.assertIn("<b>bold</b>", "".join(paragraph.itertext()))

    def test_a_backslash_escapes_any_punctuation(self):
        self.assertEqual(self.tree("\\$5, \\~, \\*, \\=").find("p").text.strip(), "$5, ~, *, =")

    def test_math_keeps_its_projection_and_prices_stay_text(self):
        paragraph = self.tree("Area $x+1$ costs $5 and $10").find("p")
        self.assertEqual([math.find("mrow/mi").text for math in paragraph.iter("math")], ["x+1"])
        self.assertIn("$5 and $10", "".join(paragraph.itertext()))
        with self.assertRaises(PdfError) as unsupported:
            self.tree("$\\frac{1}{2}$")
        self.assertEqual(unsupported.exception.code, ErrorCode.UNSUPPORTED_CAPABILITY)

    def test_images_are_embedded_from_files_under_the_source_directory(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")
        source = self.root / "in"
        source.mkdir()
        policy = SecurityPolicy(source, self.root)
        (source / "chart.png").write_bytes(png_bytes())
        (source / "my chart.png").write_bytes(png_bytes())
        (self.root / "outside.png").write_bytes(png_bytes())
        for reference in ("chart.png", str(source / "chart.png"), "my%20chart.png"):
            image = self.tree(f"![Burn-down]({reference})", policy).find("p/img")
            self.assertEqual(image.get("alt"), "Burn-down")
            self.assertTrue(image.get("src").startswith("data:image/png;base64,"), reference)
        for reference in (
            "https://acme.example/chart.png", "../outside.png",
            str(self.root / "outside.png"), "data:image/png;base64,AAAA",
        ):
            with self.assertRaises(PdfError) as rejected:
                self.tree(f"![Chart]({reference})", policy)
            self.assertEqual(rejected.exception.code, ErrorCode.UNSAFE_INPUT, reference)


@unittest.skipUnless(production_available(), "production PDF dependencies/fonts not installed")
class MarkdownPdfTests(unittest.TestCase):
    def test_every_construct_reaches_the_pdf_as_structure(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chart.png").write_bytes(png_bytes())
            source, output = root / "report.md", root / "report.pdf"
            source.write_text(FIXTURE, encoding="utf-8")
            rendered = subprocess.run(
                [sys.executable, str(ROOT / "scripts/md_to_pdf.py"), str(source), str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            validated = subprocess.run(
                [sys.executable, str(ROOT / "scripts/validate_pdf.py"), str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(
                json.loads(validated.stdout)["subject"]["sha256"],
                json.loads(rendered.stdout)["output"]["sha256"],
            )

            reader = PdfReader(str(output))
            text = "\n".join(page.extract_text() for page in reader.pages)
            layout = [
                line.rstrip()
                for page in reader.pages
                for line in page.extract_text(extraction_mode="layout").splitlines()
                if line.strip()
            ]
            links = [
                annotation.get_object()["/A"].get("/URI")
                for page in reader.pages
                for annotation in page.get("/Annots") or []
            ]
            images = sum(len(page.images) for page in reader.pages)

        # No markup survives as characters.
        for markup in ("*", "_Infrastructure_", "`", "](", "![", "|", "#", "---"):
            self.assertNotIn(markup, text)
        for line in layout:
            self.assertNotRegex(line, r"^\s*[-+] ")  # a nested item flattened to its marker
            self.assertNotRegex(line, r"^\s*>")  # a block quote marker
            self.assertNotRegex(line, r"^\s*(?:[-*_]\s*){3,}$")  # a horizontal rule

        def line_of(fragment: str) -> str:
            return next(line for line in layout if fragment in line)

        def column(fragment: str) -> int:
            line = line_of(fragment)
            return len(line) - len(line.lstrip())

        # Nesting survives as indentation, numbering as list markers.
        self.assertLess(column("P1 items"), column("Android crash on resume"))
        self.assertEqual(column("Android crash on resume"), column("CDN contract renewal"))
        self.assertLess(column("CDN contract renewal"), column("Owner: Infrastructure"))
        self.assertLess(column("Close the P1 blockers"), column("Ship the crash fix"))
        for fragment, number in (
            ("Confirm the release scope", "1."), ("Close the P1 blockers", "2."),
            ("Ship the crash fix", "1."), ("Renew the CDN contract", "2."),
            ("Schedule the security review", "3."),
        ):
            self.assertEqual(line_of(fragment).split()[0], number, fragment)

        # A table keeps its header and its rows as aligned columns.
        self.assertTrue(any(re.fullmatch(r"\s*Item\s+Owner\s+Due", line) for line in layout), layout)
        self.assertRegex(line_of("Crash fix"), r"Crash fix\s+Mobile\s+2026-09-09")
        self.assertRegex(line_of("CDN renewal"), r"CDN renewal\s+Infra\s+2026-09-13")

        # Emphasis is formatting, a link is an annotation, an image is a picture.
        self.assertIn("Prepared by the delivery team on 2026-09-15. Overall status: at risk.", text)
        self.assertIn("The launch date holds only if the P1 items close this week.", text)
        self.assertIn("./build --release", text)
        self.assertIn("https://acme.example/checklist", links)
        self.assertGreaterEqual(images, 1)


if __name__ == "__main__":
    unittest.main()
