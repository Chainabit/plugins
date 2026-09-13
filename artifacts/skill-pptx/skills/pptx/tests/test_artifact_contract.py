from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dependency_available() -> bool:
    try:
        import pptx  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(dependency_available(), "python-pptx is not installed")
class PptxArtifactContractTests(unittest.TestCase):
    def render(self, font: str | None = None, palette: dict | None = None) -> tuple[Path, dict, tempfile.TemporaryDirectory]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        spec = {
            "title": "Türkçe Bilim Sunumu",
            "slides": [
                {"layout": "title", "title": "Çağrı ve Bilim", "subtitle": "ğüşöçıİĞÜŞÖÇ"},
                {"layout": "content", "title": "Bulgular", "bullets": ["Doğrulanmış içerik", "Güvenli çıktı", "مرحبا بالعالم"]},
            ],
        }
        if font is not None:
            spec["font"] = font
        if palette is not None:
            spec["palette"] = palette
        source = root / "spec.json"
        output = root / "deck.pptx"
        source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deck_pptx.py"), str(source), str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        frame = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(frame["schema"], "chainabit.pptx.execution/v1")
        self.assertEqual(frame["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        return output, frame, temporary

    def test_default_and_override_typography_validate_exact_bytes(self) -> None:
        for requested, expected in ((None, "IBM Plex Sans"), ("Avenir Next", "Avenir Next")):
            output, produced, temporary = self.render(requested)
            try:
                validated = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/validate_pptx.py"), str(output)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(validated.returncode, 0, validated.stderr)
                frame = json.loads(validated.stdout.strip().splitlines()[-1])
                self.assertEqual(frame["validator"], "skill-pptx.validate_pptx")
                self.assertEqual(frame["subject"]["sha256"], produced["output"]["sha256"])
                self.assertEqual(frame["typography"]["family"], expected)
                self.assertEqual(frame["typography"]["fallbacks"], ["IBM Plex Sans Arabic"])
                with zipfile.ZipFile(output) as package:
                    slides = b"".join(
                        package.read(name)
                        for name in package.namelist()
                        if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                    )
                self.assertIn(b"327B61", slides)
            finally:
                temporary.cleanup()

    def test_complete_custom_palette_replaces_the_default(self) -> None:
        palette = {
            "background": "#FFFFFF", "surface": "#FDFBFF", "ink": "#2D123D",
            "body": "#4C2C5B", "muted": "#6B4C7A", "rule": "#DEC9EA",
            "accent": "#6D28D9",
        }
        output, _produced, temporary = self.render("Avenir Next", palette)
        try:
            with zipfile.ZipFile(output) as package:
                slides = b"".join(
                    package.read(name)
                    for name in package.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                )
            self.assertIn(b"6D28D9", slides)
            self.assertNotIn(b"327B61", slides)
        finally:
            temporary.cleanup()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "partial.json"
            source.write_text(json.dumps({"title": "No blend", "palette": {"accent": "#6D28D9"}, "slides": [{"layout": "closing", "title": "Done"}]}), encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, str(ROOT / "scripts/deck_pptx.py"), str(source), str(root / "out.pptx"), "--validate-only"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("palette: must include every role", rejected.stderr)

    def test_corrupt_and_missing_relationship_packages_are_rejected(self) -> None:
        output, _produced, temporary = self.render()
        try:
            corrupt = output.with_name("corrupt.pptx")
            corrupt.write_bytes(b"PK\x03\x04not-an-ooxml-package")
            self.assertEqual(subprocess.run(
                [sys.executable, str(ROOT / "scripts/validate_pptx.py"), str(corrupt)],
                capture_output=True,
                check=False,
            ).returncode, 1)

            broken = output.with_name("broken-relationship.pptx")
            with zipfile.ZipFile(output) as source, zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == "ppt/slides/_rels/slide1.xml.rels":
                        data, replacements = re.subn(
                            rb"\.\./slideLayouts/slideLayout\d+\.xml",
                            b"../missing/layout.xml",
                            data,
                            count=1,
                        )
                        self.assertEqual(replacements, 1)
                    target.writestr(info.filename, data)
            rejected = subprocess.run(
                [sys.executable, str(ROOT / "scripts/validate_pptx.py"), str(broken)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("references missing", rejected.stderr.lower())
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
