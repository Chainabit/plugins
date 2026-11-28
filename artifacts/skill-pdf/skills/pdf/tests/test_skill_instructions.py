"""The report spec printed in SKILL.md is the one the renderer accepts.

A model that is not shown the spec format reads the scripts to find it, and a
follow-up turn spent 17 tool calls doing that and delivered no file. The example
in SKILL.md is therefore an executable contract: it must validate, render, and
document every field it uses.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system import PdfService  # noqa: E402
from pdf_system.service import PALETTE_KEYS  # noqa: E402

SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")


def production_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import weasyprint  # noqa: F401
    except Exception:
        return False
    fonts = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", ""))
    return fonts.joinpath("IBMPlexSans-Regular.ttf").is_file()


def installed(family: str) -> bool:
    try:
        found = subprocess.run(
            ["fc-match", "-f", "%{family}", family], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return family.casefold() in found.casefold()


def example_spec() -> dict:
    fences = re.findall(r"```json\n(.*?)\n```", SKILL, re.S)
    assert len(fences) == 1, "SKILL.md must carry exactly one complete report-spec example"
    return json.loads(fences[0])


class SkillExampleTests(unittest.TestCase):
    def test_the_example_is_a_valid_report_spec(self):
        self.assertEqual(PdfService.validate_report(example_spec()), [])

    def test_the_example_names_every_palette_role_and_no_other(self):
        self.assertEqual(set(example_spec()["palette"]), set(PALETTE_KEYS))

    def test_every_field_the_example_uses_is_documented(self):
        spec = example_spec()
        fields = set(spec) | {key for block in spec["blocks"] for key in block}
        described = SKILL[SKILL.index("\nFields:\n"):]
        for field in sorted(fields | set(PALETTE_KEYS)):
            self.assertTrue(
                f"`{field}`" in described or f'"{field}"' in described,
                f"{field} is used by the example but not described under Fields",
            )

    def test_the_example_shows_the_columns_and_type_size_a_menu_needs(self):
        spec = example_spec()
        self.assertEqual(spec["columns"], 2)
        self.assertGreaterEqual(spec["fontSize"], 14)

    def test_the_instructions_treat_a_described_feel_as_visual_direction(self):
        flat = " ".join(SKILL.split())
        for phrase in ("described feel or style", "hand-made", "a layout", "reading condition"):
            self.assertIn(phrase, flat)
        # The old rule opened the custom look only for a named brand, guide, template, reference, font or palette.
        self.assertNotIn("A user brand, guide, template, reference, font, or palette wins", flat)

    def test_the_documented_fonts_are_the_ones_the_instructions_name(self):
        for family in ("IBM Plex Sans", "DejaVu Serif", "DejaVu Sans"):
            self.assertIn(family, SKILL)


@unittest.skipUnless(production_available() and installed("DejaVu Serif"), "production PDF dependencies/fonts not installed")
class SkillExampleRenderTests(unittest.TestCase):
    def run_script(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "report_pdf.py"), *arguments], capture_output=True, text=True, check=False)

    def test_the_documented_commands_validate_and_render_the_example_on_one_page(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "menu.json", Path(tmp) / "menu.pdf"
            source.write_text(json.dumps(example_spec()), encoding="utf-8")
            checked = self.run_script(str(source), str(output), "--validate-only")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            rendered = self.run_script(str(source), str(output), "--pages", "1")
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            result = json.loads(rendered.stdout)
            self.assertEqual(result["output"]["pages"], 1)
            self.assertEqual(result["typography"], {"family": "DejaVu Serif", "source": "user_override"})
            text = "".join(PdfReader(str(output)).pages[0].extract_text().split()).upper()
            self.assertIn("48-HOURSOURDOUGH", text)
            self.assertNotIn("CHAINABIT", text)


if __name__ == "__main__":
    unittest.main()
