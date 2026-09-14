from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_PALETTE = {
    "background": "#FFFFFF", "surface": "#FDFBFF", "ink": "#2D123D",
    "body": "#4C2C5B", "muted": "#6B4C7A", "rule": "#DEC9EA",
    "accent": "#6D28D9",
}


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
        for requested, expected, palette in ((None, "IBM Plex Sans", None), ("Avenir Next", "Avenir Next", CUSTOM_PALETTE)):
            output, produced, temporary = self.render(requested, palette)
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
                self.assertIn((b"6D28D9" if palette else b"327B61"), slides)
            finally:
                temporary.cleanup()

    def test_complete_custom_palette_replaces_the_default(self) -> None:
        output, _produced, temporary = self.render("Avenir Next", CUSTOM_PALETTE)
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


class PptxSpecValidationTests(unittest.TestCase):
    """Palette resolution is testable even outside the optional PPTX runtime."""

    def test_noncanonical_font_requires_complete_palette(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "spec.json"
            output = root / "deck.pptx"
            spec = {"title": "Customer deck", "font": "Avenir Next", "slides": [{"layout": "closing", "title": "Done"}]}
            source.write_text(json.dumps(spec), encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, str(ROOT / "scripts/deck_pptx.py"), str(source), str(output), "--validate-only"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("required for a non-Chainabit font override", rejected.stderr)

            spec["palette"] = CUSTOM_PALETTE
            source.write_text(json.dumps(spec), encoding="utf-8")
            accepted = subprocess.run(
                [sys.executable, str(ROOT / "scripts/deck_pptx.py"), str(source), str(output), "--validate-only"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)


#: Host locations an instruction must never name -- the set the repository's
#: portability lint rejects in SKILL.md. A printed instruction is held to it too.
HOST_LOCATIONS = ("/workspace", "/sandbox", ".skills", "/home/", "/opt/")


def reportlab_fonts() -> Path | None:
    """reportlab's own bundled TrueType faces, when reportlab is installed."""
    try:
        import reportlab
    except ImportError:
        return None
    fonts = Path(reportlab.__file__).resolve().parent / "fonts"
    return fonts if (fonts / "Vera.ttf").is_file() and (fonts / "VeraBd.ttf").is_file() else None


class DeckPdfHandoffTests(unittest.TestCase):
    """deck_pdf.py's output: a result line, a hand-off line, then the execution frame.

    The hand-off states a fact: the PDF is checked by the pdf capability's
    validator when it is delivered. That validator ships in another bundle and
    runs as part of delivery, so the line names no script for the caller to run
    and no path -- a caller works from the workspace root, where no path spelled
    here is guaranteed to exist.
    """

    def assertHandoff(self, line: str, output: str) -> None:
        self.assertTrue(line.startswith("Next: "), line)
        self.assertIn("skill-pdf.validate_pdf", line)
        self.assertIn(output, line)
        remainder = line.replace(output, "")
        self.assertNotIn(".py", remainder, "the hand-off must not name a script to run")
        self.assertNotIn("/", remainder, "the hand-off must spell no path")
        for location in HOST_LOCATIONS:
            self.assertNotIn(location, line)

    def test_handoff_states_the_delivery_check_and_names_no_script(self) -> None:
        scripts = str(ROOT / "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        deck_pdf = importlib.import_module("deck_pdf")
        self.assertEqual(deck_pdf.PDF_VALIDATOR_ID, "skill-pdf.validate_pdf")
        line = deck_pdf.validation_handoff("out/q3.pdf")
        self.assertNotIn("\n", line)
        self.assertHandoff(line, "out/q3.pdf")

    @unittest.skipUnless(reportlab_fonts(), "reportlab is not installed")
    def test_render_prints_result_handoff_and_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            # The host provides the artifact font directory. A stand-in under the
            # same file names lets this contract run wherever reportlab does;
            # glyph coverage is not what it asserts.
            fonts = workspace / "fonts"
            fonts.mkdir()
            shutil.copyfile(reportlab_fonts() / "Vera.ttf", fonts / "IBMPlexSans-Regular.ttf")
            shutil.copyfile(reportlab_fonts() / "VeraBd.ttf", fonts / "IBMPlexSans-SemiBold.ttf")
            spec = {
                "title": "Quarterly review",
                "slides": [
                    {"layout": "title", "title": "Quarterly review", "subtitle": "Regional summary"},
                    {"layout": "content", "title": "Findings", "bullets": ["Volume grew", "Costs held flat"]},
                ],
            }
            (workspace / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            environment = {**os.environ, "CHAINABIT_ARTIFACT_FONT_DIR": str(fonts)}
            environment.pop("CHAINABIT_ARTIFACT_FONT_FAMILY", None)
            # The runtime contract: the working directory is the workspace root,
            # and the script is addressed wherever its bundle actually is.
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/deck_pdf.py"), "spec.json", "deck.pdf"],
                cwd=workspace, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(len(lines), 3, result.stdout)
            self.assertTrue(lines[0].startswith("OK: wrote deck.pdf ("), lines[0])
            self.assertHandoff(lines[1], "deck.pdf")
            frame = json.loads(lines[2])
            produced = (workspace / "deck.pdf").read_bytes()
            self.assertTrue(produced.startswith(b"%PDF-"))
            self.assertEqual(frame["schema"], "chainabit.pdf.execution/v1")
            self.assertEqual(frame["generator"], "skill-pptx.deck_pdf")
            self.assertEqual(frame["output"]["mime"], "application/pdf")
            self.assertEqual(frame["output"]["bytes"], len(produced))
            self.assertEqual(frame["output"]["sha256"], hashlib.sha256(produced).hexdigest())
            self.assertEqual(frame["typography"], {"family": "IBM Plex Sans", "source": "chainabit_default"})


if __name__ == "__main__":
    unittest.main()
