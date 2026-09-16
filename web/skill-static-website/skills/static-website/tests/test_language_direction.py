from __future__ import annotations

"""Regression coverage for language-agnostic generation.

Direction and the two accessibility-chrome labels are resolved from the
spec's own declared `site.lang` / optional overrides, not from a per-language
conditional baked into this script. These tests exist because, before this
change, `render_page` always wrote `<html lang="...">` with no `dir`
attribute at all -- an Arabic or Hebrew site rendered with every block
left-aligned in a real browser -- and the skip-link/nav landmark text was a
literal English string on every generated page regardless of the declared
language.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_FILES = (
    "IBMPlexSans-Regular.woff2",
    "IBMPlexSans-SemiBold.woff2",
    "IBMPlexSansArabic-Regular.woff2",
    "IBMPlexSansArabic-SemiBold.woff2",
)


class StaticWebsiteDirectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        fonts = self.root / "fonts"
        fonts.mkdir()
        for name in FONT_FILES:
            (fonts / name).write_bytes(b"wOF2unit-test-font")
        self.env = {**os.environ, "CHAINABIT_ARTIFACT_FONT_DIR": str(fonts)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _base_spec(self) -> dict:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--template", "landing", "--print-spec"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def _build(self, spec: dict, name: str) -> Path:
        source = self.root / f"{name}.json"
        source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        site = self.root / name
        built = subprocess.run(
            [sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--spec", str(source), str(site)],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        checked = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_site.py"), str(site), "--strict"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        return site

    def test_arabic_lang_produces_rtl_direction(self) -> None:
        spec = self._base_spec()
        spec["site"]["lang"] = "ar"
        spec["site"]["title"] = "نورث ويند"
        site = self._build(spec, "arabic-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="ar" dir="rtl">', html)

    def test_regional_and_script_suffixes_do_not_change_direction(self) -> None:
        spec = self._base_spec()
        spec["site"]["lang"] = "ar-EG"
        site = self._build(spec, "arabic-eg-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn('dir="rtl"', html)

    def test_english_and_unset_lang_stay_ltr(self) -> None:
        spec = self._base_spec()
        site = self._build(spec, "default-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="en" dir="ltr">', html)

    def test_turkish_lang_stays_ltr_with_diacritics_preserved(self) -> None:
        spec = self._base_spec()
        spec["site"]["lang"] = "tr"
        spec["pages"][0]["sections"][0]["heading"] = "Çeyreklik Sipariş Özeti: Ğğ Şş Iı İi Öö Üü"
        site = self._build(spec, "turkish-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn('dir="ltr"', html)
        self.assertIn("Çeyreklik Sipariş Özeti: Ğğ Şş Iı İi Öö Üü", html)

    def test_skip_link_and_nav_labels_default_to_english_and_are_overridable(self) -> None:
        spec = self._base_spec()
        site = self._build(spec, "default-labels-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn(">Skip to content<", html)

        spec["site"]["lang"] = "ar"
        spec["site"]["skipLinkLabel"] = "تخطَّ إلى المحتوى"
        spec["site"]["navLabel"] = "التنقل الرئيسي"
        # A nav landmark is only emitted for 2+ pages carrying `nav`.
        spec["pages"][0]["nav"] = "الرئيسية"
        spec["pages"].append({"path": "about.html", "title": "من نحن", "nav": "من نحن", "sections": [
            {"type": "hero", "heading": "من نحن", "text": "نبذة عنا."},
        ]})
        site = self._build(spec, "arabic-labels-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn(">تخطَّ إلى المحتوى<", html)
        self.assertNotIn("Skip to content", html)
        self.assertIn('aria-label="التنقل الرئيسي"', html)

    def test_mixed_arabic_and_latin_content_is_preserved_verbatim(self) -> None:
        """Embedded Latin technical terms, URLs and numbers inside RTL
        content must not be translated, reordered or corrupted -- direction
        is a container property; content passes through unchanged."""
        spec = self._base_spec()
        spec["site"]["lang"] = "ar"
        spec["pages"][0]["sections"][0]["text"] = (
            "راجع الرابط https://example.com/report للمزيد، أو استخدم ميزة Dashboard Pro، الإصدار 42."
        )
        site = self._build(spec, "mixed-arabic-site")
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn("https://example.com/report", html)
        self.assertIn("Dashboard Pro", html)
        self.assertIn("42", html)


if __name__ == "__main__":
    unittest.main()
