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
    def render(self, font: str | None = None) -> tuple[Path, dict, tempfile.TemporaryDirectory]:
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
            finally:
                temporary.cleanup()

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
