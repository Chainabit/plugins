"""A customer's document carries the customer's look, not Chainabit's mark.

A bakery asked for a printable menu and received it with a `CHAINABIT` line
printed at the foot of the page. The mark appeared whenever the request named no
palette and no font, which is exactly the document a customer prints and hands to
other people. The renderer no longer prints it, with or without a palette; the
producer is recorded in the PDF's own `Creator` metadata field instead.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system import PdfService, SecurityPolicy  # noqa: E402
from pdf_system.models import PageGeometry  # noqa: E402
from pdf_system.service import DEFAULT_PALETTE  # noqa: E402


def production_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import weasyprint  # noqa: F401
    except Exception:
        return False
    fonts = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", ""))
    return fonts.joinpath("IBMPlexSans-Regular.ttf").is_file()


def printed(page) -> str:
    """The page's text with spacing removed: the old mark was letter-spaced and extracted as C H A I N ..."""
    return "".join(page.extract_text().split()).upper()


CUSTOM_PALETTE = {
    "background": "#FBF4E6", "surface": "#F3E6CC", "ink": "#3B2A1E", "body": "#4A3A2E",
    "muted": "#7A6652", "rule": "#D9C3A0", "accent": "#B5651D", "accentInk": "#FFF8EC",
}


class PageFurnitureTests(unittest.TestCase):
    """No renderer needed: the stylesheet is plain string assembly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = PdfService(SecurityPolicy(Path(self.tmp.name), Path(self.tmp.name)))
        self.geometry = PageGeometry.from_spec("A4")

    def document(self, palette: dict) -> str:
        with patch.object(PdfService, "_font_css", return_value=""):
            return self.service._html_document("<p>x</p>", "IBM Plex Sans", palette, self.geometry)

    def test_the_default_and_a_custom_palette_print_no_brand_mark(self):
        for palette in (DEFAULT_PALETTE, CUSTOM_PALETTE):
            html = self.document(palette)
            self.assertNotIn("CHAINABIT", html)
            self.assertNotIn("@bottom-left", html)

    def test_the_page_counter_is_the_only_automatic_page_furniture(self):
        html = self.document(DEFAULT_PALETTE)
        self.assertIn("@bottom-right{content:counter(page)", html)


@unittest.skipUnless(production_available(), "production PDF dependencies/fonts not installed")
class RenderedDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = PdfService(SecurityPolicy(self.root, self.root))

    def render_report(self, spec: dict):
        from pypdf import PdfReader

        source = self.root / "report.json"
        source.write_text(json.dumps(spec), encoding="utf-8")
        self.service.generate_report(source, self.root / "out.pdf")
        return PdfReader(str(self.root / "out.pdf"))

    def test_a_default_report_prints_no_brand_and_records_its_producer_in_metadata(self):
        reader = self.render_report({"title": "Menu", "blocks": [{"type": "paragraph", "text": "Sourdough 4.50"}]})
        self.assertNotIn("CHAINABIT", printed(reader.pages[0]))
        self.assertEqual(reader.metadata["/Creator"], "chainabit-pdf")

    def test_a_default_markdown_document_prints_no_brand(self):
        from pypdf import PdfReader

        source = self.root / "doc.md"
        source.write_text("# Menu\n\nSourdough 4.50\n", encoding="utf-8")
        self.service.generate_markdown(source, self.root / "doc.pdf", title="Menu")
        self.assertNotIn("CHAINABIT", printed(PdfReader(str(self.root / "doc.pdf")).pages[0]))


if __name__ == "__main__":
    unittest.main()
