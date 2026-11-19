from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pdf_system.errors import ErrorCode, PdfError
from pdf_system.models import Limits
from pdf_system.verification import verify_pdf


def _dependencies_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def _pdf_with_standard_font(text: str = "standard font, not embedded") -> bytes:
    from reportlab.pdfgen import canvas
    import io

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(960, 540))
    page.setFont("Helvetica", 20)
    page.drawString(50, 50, text)
    page.showPage()
    page.save()
    return buffer.getvalue()


class UnembeddedFontTests(unittest.TestCase):
    def test_non_embedded_font_is_reported_not_rejected(self) -> None:
        if not _dependencies_available():
            self.skipTest("pypdf and reportlab are not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "standard.pdf"
            path.write_bytes(_pdf_with_standard_font())
            verification = verify_pdf(path, Limits())
        self.assertEqual(verification.pages, 1)
        self.assertIn("unembedded_fonts=Helvetica", verification.warnings)

    def test_standard_font_that_loses_turkish_letters_is_rejected(self) -> None:
        if not _dependencies_available():
            self.skipTest("pypdf and reportlab are not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "turkish.pdf"
            path.write_bytes(_pdf_with_standard_font("Çağrı Şişli İğne ıspanak"))
            with self.assertRaises(PdfError) as raised:
                verify_pdf(path, Limits())
        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION_FAILURE)
        self.assertIn("non-embedded font", str(raised.exception))
        self.assertIn("Helvetica", str(raised.exception))

    def test_standard_font_text_it_can_encode_is_still_delivered(self) -> None:
        if not _dependencies_available():
            self.skipTest("pypdf and reportlab are not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latin1.pdf"
            path.write_bytes(_pdf_with_standard_font("Görev ürün Özeti çözüm ■ marker"))
            verification = verify_pdf(path, Limits())
        self.assertTrue(
            any(w.startswith("unembedded_fonts=Helvetica") for w in verification.warnings)
        )

    def test_square_on_a_page_drawn_with_an_embedded_font_is_text(self) -> None:
        if not _dependencies_available():
            self.skipTest("pypdf and reportlab are not installed")
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
        import io

        font_file = next(
            (
                Path(candidate)
                for candidate in (
                    "/Library/Fonts/Arial.ttf",
                    "/System/Library/Fonts/Supplemental/Arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                )
                if Path(candidate).is_file()
            ),
            None,
        )
        if font_file is None:
            self.skipTest("no TrueType font is installed to embed")
        pdfmetrics.registerFont(TTFont("EmbeddedLegend", str(font_file)))
        buffer = io.BytesIO()
        page = canvas.Canvas(buffer, pagesize=(960, 540))
        # ReportLab lists Helvetica in the page resources even when it is unused.
        page.setFont("EmbeddedLegend", 20)
        page.drawString(50, 50, "Legend \u25a0Revenue Q1\u25a0Q2")
        page.showPage()
        page.save()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legend.pdf"
            path.write_bytes(buffer.getvalue())
            verification = verify_pdf(path, Limits())
        self.assertEqual(verification.pages, 1)


if __name__ == "__main__":
    unittest.main()
