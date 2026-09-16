from __future__ import annotations

"""Regression coverage for deck_pdf.py's language handling.

Two real, evidenced defects motivate this file:

1. `wrap()` used to delegate to `reportlab.lib.utils.simpleSplit`, which
   tokenizes purely on `str.split()` -- ASCII whitespace. A Chinese or
   Japanese sentence has none, so the whole sentence became one token placed
   on a line regardless of width, and it ran off the slide edge at exit 0.
2. `deck_pdf.py` draws text with `canvas.drawString`, which has no Unicode
   Bidi reordering and no Arabic contextual shaping, and neither library
   (`python-bidi`, `arabic-reshaper`) is installed in the sandbox image. A
   predominantly right-to-left deck used to render at exit 0 with every
   Arabic/Hebrew line disconnected and in the wrong visual order.

`deck_pptx.py`'s own `.pptx` output is a different, unaffected code path --
PowerPoint/Keynote/Google Slides shape and reorder Unicode text themselves --
so the RTL refusal in `deck_pdf.py` must not also block the same spec's
`.pptx` build.
"""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def reportlab_available() -> bool:
    try:
        import reportlab  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(reportlab_available(), "reportlab is not installed")
class WrapLineBreakingTests(unittest.TestCase):
    def setUp(self) -> None:
        import reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        fonts = Path(reportlab.__file__).resolve().parent / "fonts"
        pdfmetrics.registerFont(TTFont("WrapTestFont", str(fonts / "Vera.ttf")))
        self.deck_pdf = importlib.import_module("deck_pdf")

    def test_space_delimited_text_still_wraps_at_word_boundaries(self) -> None:
        lines = self.deck_pdf.wrap(
            "The quick brown fox jumps over the lazy dog and keeps running",
            "WrapTestFont", 14, 200,
        )
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self._width(line), 200)
        self.assertEqual(" ".join(lines), "The quick brown fox jumps over the lazy dog and keeps running")

    def test_unspaced_cjk_text_wraps_between_characters_within_the_box(self) -> None:
        sentence = "这是一份很长的中文句子用来测试没有空格时的自动换行是否能够正常工作而不会超出边界"
        lines = self.deck_pdf.wrap(sentence, "WrapTestFont", 14, 150)
        self.assertGreater(len(lines), 1, "an unspaced CJK sentence must still produce more than one line")
        for line in lines:
            self.assertLessEqual(self._width(line), 150, f"line {line!r} overflows the box")
        self.assertEqual("".join(lines), sentence)

    def test_a_single_character_wider_than_the_box_still_terminates(self) -> None:
        lines = self.deck_pdf.wrap("W", "WrapTestFont", 400, 1)
        self.assertEqual(lines, ["W"])

    def _width(self, text: str) -> float:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        return stringWidth(text, "WrapTestFont", 14)


class RtlDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        self.deck_pdf = importlib.import_module("deck_pdf")

    def test_majority_arabic_slide_text_is_detected(self) -> None:
        spec = {"slides": [{"layout": "content", "title": "تقرير الأداء", "bullets": ["ارتفعت الإيرادات", "انخفضت التكاليف"]}]}
        self.assertTrue(self.deck_pdf._spec_is_predominantly_rtl(spec))

    def test_a_single_embedded_arabic_word_does_not_flip_an_english_deck(self) -> None:
        spec = {"slides": [{"layout": "content", "title": "Quarterly Review", "bullets": ["Revenue grew, see مرحبا for context", "Costs held flat"]}]}
        self.assertFalse(self.deck_pdf._spec_is_predominantly_rtl(spec))

    def test_a_purely_english_deck_is_not_rtl(self) -> None:
        spec = {"slides": [{"layout": "title", "title": "Quarterly Review", "subtitle": "Regional summary"}]}
        self.assertFalse(self.deck_pdf._spec_is_predominantly_rtl(spec))


@unittest.skipUnless(reportlab_available(), "reportlab is not installed")
class DeckPdfRtlRefusalTests(unittest.TestCase):
    """End-to-end: the CLI refuses a predominantly-RTL deck's PDF companion,
    by name, while the SAME spec's .pptx build is unaffected."""

    def _reportlab_fonts(self) -> Path:
        import reportlab

        return Path(reportlab.__file__).resolve().parent / "fonts"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        fonts = self.workspace / "fonts"
        fonts.mkdir()
        import shutil

        source_fonts = self._reportlab_fonts()
        shutil.copyfile(source_fonts / "Vera.ttf", fonts / "IBMPlexSans-Regular.ttf")
        shutil.copyfile(source_fonts / "VeraBd.ttf", fonts / "IBMPlexSans-SemiBold.ttf")
        self.env = {**os.environ, "CHAINABIT_ARTIFACT_FONT_DIR": str(fonts)}
        self.env.pop("CHAINABIT_ARTIFACT_FONT_FAMILY", None)
        self.spec = {
            "title": "تقرير الأداء الفصلي",
            "slides": [
                {"layout": "title", "title": "تقرير الأداء الفصلي", "subtitle": "ملخص إقليمي"},
                {"layout": "content", "title": "أبرز النتائج", "bullets": ["ارتفعت الإيرادات بنسبة كبيرة", "انخفضت التكاليف التشغيلية"]},
            ],
        }
        (self.workspace / "spec.json").write_text(json.dumps(self.spec, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_deck_pdf_refuses_by_name(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deck_pdf.py"), "spec.json", "deck.pdf"],
            cwd=self.workspace, env=self.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("right-to-left", result.stderr)
        self.assertFalse((self.workspace / "deck.pdf").exists())

    def test_deck_pptx_is_unaffected_by_the_same_spec(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deck_pptx.py"), "spec.json", "deck.pptx"],
            cwd=self.workspace, env=self.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.workspace / "deck.pptx").exists())


if __name__ == "__main__":
    unittest.main()
