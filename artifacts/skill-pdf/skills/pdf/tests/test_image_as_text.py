"""A picture written as text never reaches a delivered PDF.

A chart that reaches this skill as a raw HTML <img> tag or an inline data: URI
is not an image here. A report escapes it and prints it, so the PDF carries
pages of base64 characters while staying well formed, painted and
font-complete. The input boundary rejects both spellings and names the
supported syntax. The validator rejects any PDF that prints image data as text.
"""
from __future__ import annotations

import base64
import json
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system import PdfService, SecurityPolicy  # noqa: E402
from pdf_system.errors import ErrorCode, PdfError  # noqa: E402
from pdf_system.models import PageGeometry  # noqa: E402
from pdf_system.safety import image_as_text, image_payload_page  # noqa: E402


def png_bytes(width: int = 120, height: int = 60, seed: int = 7) -> bytes:
    """A valid, poorly compressible PNG built with the standard library."""
    noise = random.Random(seed)
    rows = b"".join(
        b"\x00" + bytes(noise.getrandbits(8) for _ in range(width * 3)) for _ in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def wrapped(text: str, width: int = 72) -> str:
    """How text extraction returns a long token that a renderer had to wrap."""
    return "\n".join(text[index:index + width] for index in range(0, len(text), width))


CHART = base64.b64encode(png_bytes()).decode("ascii")
SUPPORTED_MARKDOWN = "![description](relative/path.png)"
SUPPORTED_REPORT = '{"type": "image", "path": "relative/path.png"}'


class ImageTextDetectionTests(unittest.TestCase):
    def test_a_wrapped_data_uri_payload_is_found(self):
        page = (
            'Quarterly revenue\n<img src="data:image/png;base64,'
            + wrapped(CHART)
            + '">\nClosing remarks.'
        )
        self.assertEqual(image_payload_page([page]), 1)

    def test_a_payload_without_a_prefix_is_found_by_its_signature(self):
        jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + png_bytes()[8:]).decode("ascii")
        for payload in (CHART, jpeg):
            self.assertEqual(
                image_payload_page(["Figure 1\n" + wrapped(payload) + "\nSource: survey"]), 1
            )

    def test_a_payload_that_starts_at_the_foot_of_a_page_is_found(self):
        # The page ends a few characters into the payload and prints its
        # footer; the payload continues at the top of the next page.
        first = (
            'Summary paragraph.\n<img src="data:image/png;base64,'
            + CHART[:40]
            + "\nCHAINABIT\n3 / 12"
        )
        second = wrapped(CHART[40:4000]) + "\nCHAINABIT\n4 / 12"
        self.assertEqual(image_payload_page(["Cover page", first, second]), 2)
        bare = "Summary paragraph.\n" + CHART[:24] + "\n3 / 12"
        self.assertEqual(image_payload_page([bare, wrapped(CHART[24:4000])]), 1)

    def test_illustrative_snippets_and_other_base64_pass(self):
        red_dot = (
            "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAFCAYAAACNbyblAAAAHElEQVQI12P4//8/w38GIAXDIB"
            "KE0DHxgljNBAAO9TXL0Y4OHwAAAABJRU5ErkJggg=="
        )
        prose = (
            "Base64 PNG data always begins with iVBORw0KGgo. A data URI such as "
            f"data:image/png;base64,{red_dot} embeds a five-pixel red dot, and "
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA... is how it looks in HTML."
        )
        certificate = (
            "-----BEGIN CERTIFICATE-----\n"
            + wrapped(base64.b64encode(bytes(range(256)) * 12).decode("ascii"), 64)
            + "\n-----END CERTIFICATE-----"
        )
        column = "\n".join(["Region", "North", "South", "East", "West", "Total"] * 60)
        for text in (prose, certificate, column, "Revenue grew in every region. " * 400):
            self.assertIsNone(image_payload_page([text]), text[:60])

    def test_image_markup_and_inline_data_are_named(self):
        self.assertEqual(image_as_text('<img src="chart.png" alt="Revenue">'), "a raw HTML <img> tag")
        self.assertEqual(image_as_text("< SVG viewBox='0 0 10 10'>"), "a raw HTML <svg> tag")
        self.assertEqual(
            image_as_text("Chart: data:image/png;base64," + CHART), "inline base64 image data"
        )
        self.assertIsNone(image_as_text("See ![Revenue by region](charts/revenue.png)."))


class InputBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source, self.output = root / "in", root / "out"
        self.source.mkdir()
        self.output.mkdir()
        self.policy = SecurityPolicy(self.source, self.output)

    def tearDown(self):
        self.tmp.cleanup()

    def markdown_error(self, text: str) -> PdfError:
        source = self.source / "report.md"
        source.write_text(text, encoding="utf-8")
        with self.assertRaises(PdfError) as caught:
            PdfService(self.policy).generate_markdown(source, self.output / "report.pdf")
        return caught.exception

    def test_markdown_image_markup_names_the_supported_syntax(self):
        error = self.markdown_error(
            '# Revenue\n\n<img src="data:image/png;base64,' + CHART + '">\n'
        )
        self.assertEqual(error.code, ErrorCode.UNSAFE_INPUT)
        self.assertIn("raw HTML <img> tag", error.message)
        self.assertIn(SUPPORTED_MARKDOWN, error.message)
        self.assertNotIn(CHART[:32], error.message)

    def test_markdown_inline_image_data_is_rejected_rather_than_printed(self):
        # As an image target a data: URI used to fail as a retryable
        # filesystem error; as a link or bare text it was printed.
        for text in (
            f"![Revenue](data:image/png;base64,{CHART})",
            f"[Revenue](data:image/png;base64,{CHART})",
            f"data:image/png;base64,{CHART}",
        ):
            error = self.markdown_error("# Revenue\n\n" + text + "\n")
            self.assertEqual(error.code, ErrorCode.UNSAFE_INPUT, text[:40])
            self.assertIn("inline base64 image data", error.message)
            self.assertIn(SUPPORTED_MARKDOWN, error.message)

    def test_report_text_holding_an_image_names_the_image_block(self):
        spec = {
            "title": "Revenue",
            "header": f"data:image/png;base64,{CHART}",
            "blocks": [
                {"type": "heading", "text": "By region"},
                {"type": "paragraph", "text": f'<img src="data:image/png;base64,{CHART}">'},
                {
                    "type": "table",
                    "columns": ["Region", "Chart"],
                    "rows": [["North", f"data:image/png;base64,{CHART}"]],
                },
            ],
        }
        problems = PdfService.validate_report(spec)
        self.assertEqual(len(problems), 1, problems)
        for where in (
            "header (inline base64 image data)",
            "blocks[1].text (a raw HTML <img> tag)",
            "blocks[2].rows[0][1] (inline base64 image data)",
        ):
            self.assertIn(where, problems[0])
        self.assertIn(SUPPORTED_REPORT, problems[0])
        self.assertNotIn(CHART[:32], problems[0])

        source = self.source / "report.json"
        source.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaises(PdfError) as caught:
            PdfService(self.policy).generate_report(source, self.output / "report.pdf")
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
        preflight = subprocess.run(
            [sys.executable, str(ROOT / "scripts/report_pdf.py"), str(source),
             str(self.output / "report.pdf"), "--validate-only"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(preflight.returncode, 1, preflight.stderr)
        self.assertEqual(json.loads(preflight.stderr)["error"]["class"], "invalid_user_input")

    def test_a_report_image_block_is_still_the_supported_path(self):
        spec = {
            "title": "Revenue",
            "blocks": [{"type": "image", "path": "charts/revenue.png", "caption": "Revenue by region"}],
        }
        self.assertEqual(PdfService.validate_report(spec), [])


def pdf_tooling_available() -> bool:
    try:
        import PIL  # noqa: F401
        import pypdf  # noqa: F401
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(pdf_tooling_available(), "pypdf, reportlab and Pillow are not installed")
class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def pdf(self, name: str, flow: list) -> Path:
        """A PDF whose only font is an embedded TrueType face."""
        import reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate

        face = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont("EmbeddedFace", str(face)))
        target = self.root / name
        SimpleDocTemplate(str(target), initialFontName="EmbeddedFace").build(flow)
        return target

    def paragraphs(self, *texts: str) -> list:
        from xml.sax.saxutils import escape

        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph

        style = ParagraphStyle("body", fontName="EmbeddedFace", fontSize=10, leading=13)
        return [Paragraph(escape(text), style) for text in texts]

    def validate(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_pdf.py"), str(path)],
            capture_output=True, text=True, check=False,
        )

    def printed_chart(self) -> Path:
        # What a report paragraph holding <img src="data:..."> produced before
        # its input was checked: pages of base64 characters.
        self.pdf("probe.pdf", [])  # registers the embedded face
        return self.pdf("printed.pdf", self.paragraphs(
            "Revenue by region.",
            f'<img src="data:image/png;base64,{CHART}">',
            "Closing remarks.",
        ))

    def test_a_pdf_that_prints_image_data_is_rejected(self):
        from pdf_system.models import Limits
        from pdf_system.verification import verify_pdf

        printed = self.printed_chart()
        with self.assertRaises(PdfError) as caught:
            verify_pdf(printed, Limits())
        self.assertEqual(caught.exception.code, ErrorCode.VALIDATION_FAILURE)
        self.assertIn("base64 image data as text on page 1", caught.exception.message)
        result = self.validate(printed)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(json.loads(result.stderr)["error"]["class"], "produced_artifact_rejected")

    def test_the_renderer_never_persists_a_pdf_that_prints_image_data(self):
        printed = self.printed_chart()

        class Backend:
            capabilities = type("Capabilities", (), {"name": "reportlab"})

            def render(self, document, geometry, metadata, destination, policy):
                shutil.copyfile(printed, destination)

        target = self.root / "delivered.pdf"
        with self.assertRaises(PdfError) as caught:
            PdfService(SecurityPolicy(self.root, self.root))._render(
                {}, Backend(), target, {"Title": "Revenue"}, PageGeometry.from_spec("A4", "portrait")
            )
        self.assertEqual(caught.exception.code, ErrorCode.VALIDATION_FAILURE)
        self.assertFalse(target.exists())

    def test_a_real_image_and_an_illustrative_snippet_pass(self):
        from reportlab.platypus import Image

        chart = self.root / "revenue.png"
        chart.write_bytes(png_bytes())
        self.pdf("probe.pdf", [])
        clean = self.pdf("clean.pdf", [
            *self.paragraphs("Revenue by region."),
            Image(str(chart), width=300, height=150),
            *self.paragraphs("PNG data in base64 begins with iVBORw0KGgo. " * 20),
        ])
        result = self.validate(clean)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no_printed_image_data", json.loads(result.stdout)["checks"])


if __name__ == "__main__":
    unittest.main()
