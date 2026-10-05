from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pdf_system.models import Limits
from pdf_system.verification import verify_pdf


def _pdf_with_standard_font() -> bytes:
    from reportlab.pdfgen import canvas
    import io

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(960, 540))
    page.setFont("Helvetica", 20)
    page.drawString(50, 50, "standard font, not embedded")
    page.showPage()
    page.save()
    return buffer.getvalue()


class UnembeddedFontTests(unittest.TestCase):
    def test_non_embedded_font_is_reported_not_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "standard.pdf"
            path.write_bytes(_pdf_with_standard_font())
            verification = verify_pdf(path, Limits())
        self.assertEqual(verification.pages, 1)
        self.assertIn("unembedded_fonts=Helvetica", verification.warnings)


if __name__ == "__main__":
    unittest.main()
