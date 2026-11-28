"""What a report spec can say about the look of a document, and what the renderer does with it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system import PdfService, SecurityPolicy  # noqa: E402


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


class FontFaceLookupTests(unittest.TestCase):
    def service(self, root: str) -> PdfService:
        return PdfService(SecurityPolicy(Path(root), Path(root)))

    def test_faces_are_requested_by_weight_not_by_style_name(self):
        queries = []

        def fake_run(command, **_):
            queries.append(command[-1])
            return subprocess.CompletedProcess(command, 0, stdout="DejaVu Serif\n" + __file__ + "\n")

        with tempfile.TemporaryDirectory() as tmp, patch("pdf_system.service.subprocess.run", fake_run):
            service = self.service(tmp)
            service._font_file("DejaVu Serif", "Regular")
            service._font_file("DejaVu Serif", "Semibold")
        self.assertEqual(queries, ["DejaVu Serif:weight=80", "DejaVu Serif:weight=180"])

    @unittest.skipUnless(installed("DejaVu Serif"), "DejaVu Serif is not installed")
    def test_a_family_without_a_semibold_face_resolves_its_bold_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(tmp)
            self.assertEqual(service._font_file("DejaVu Serif", "Regular").name, "DejaVuSerif.ttf")
            self.assertEqual(service._font_file("DejaVu Serif", "Semibold").name, "DejaVuSerif-Bold.ttf")


if __name__ == "__main__":
    unittest.main()
