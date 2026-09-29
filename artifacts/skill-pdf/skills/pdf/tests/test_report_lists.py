"""Structured report lists retain their meaning in both rendering backends."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.backends import ReportLabRenderer
from pdf_system.models import PageGeometry
from pdf_system.service import DEFAULT_PALETTE


def renderer_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


class ReportListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = SecurityPolicy((self.root,), (self.root,), self.root)
        self.geometry = PageGeometry.from_spec('A4', 'portrait')
        self.report = {
            'title': 'Deployment procedure',
            'palette': DEFAULT_PALETTE,
            'blocks': [
                {'type': 'numbered', 'items': ['Build <source>', 'Validate', 'Publish']},
                {'type': 'bullets', 'items': ['Reversible', 'Auditable']},
                {'type': 'numbered', 'items': ['Monitor', 'Review']},
            ],
        }

    def test_html_preserves_ordered_and_unordered_lists(self):
        with patch.object(PdfService, '_font_css', return_value=''):
            html = PdfService(self.policy)._report_html(
                self.report, self.policy, 'IBM Plex Sans', DEFAULT_PALETTE, False, self.geometry
            )
        self.assertEqual(html.count('<ol>'), 2)
        self.assertEqual(html.count('<ul>'), 1)
        self.assertIn('<ol><li>Build &lt;source&gt;</li><li>Validate</li><li>Publish</li></ol>', html)

    @unittest.skipUnless(renderer_available(), 'ReportLab and pypdf are not installed')
    def test_reportlab_numbers_items_and_restarts_each_list(self):
        import reportlab
        from pypdf import PdfReader
        # Bundled test fonts exercise real PDF structure, not brand/font equivalence.
        fonts = Path(reportlab.__file__).resolve().parent / 'fonts'
        font_root = self.root / 'fonts'
        font_root.mkdir()
        shutil.copyfile(fonts / 'Vera.ttf', font_root / 'IBMPlexSans-Regular.ttf')
        shutil.copyfile(fonts / 'VeraBd.ttf', font_root / 'IBMPlexSans-SemiBold.ttf')
        output = self.root / 'procedure.pdf'
        with patch.dict(os.environ, {'CHAINABIT_ARTIFACT_FONT_DIR': str(font_root)}):
            ReportLabRenderer().render(self.report, self.geometry, {}, output, self.policy)
        text = '\n'.join(page.extract_text() for page in PdfReader(output).pages)
        for item in ('1. Build <source>', '2. Validate', '3. Publish', '1. Monitor', '2. Review'):
            self.assertIn(item, text)
        self.assertEqual(len(PdfReader(output).pages), 1)
