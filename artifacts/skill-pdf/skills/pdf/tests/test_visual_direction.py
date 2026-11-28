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
from pdf_system.backends import capability_registry  # noqa: E402
from pdf_system.models import DocumentRequirements, PageGeometry  # noqa: E402
from pdf_system.service import DEFAULT_PALETTE  # noqa: E402


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


PALETTE = {
    "background": "#FBF3E4", "surface": "#F3E4C8", "ink": "#3E2A1D", "body": "#4A3526",
    "muted": "#7A6450", "rule": "#D8C09A", "accent": "#B5541C", "accentInk": "#FFF7E8",
}


def spec(**extra) -> dict:
    return {"title": "Menu", "blocks": [{"type": "paragraph", "text": "Sourdough"}], **extra}


class LayoutControlValidationTests(unittest.TestCase):
    def problems(self, **extra) -> list[str]:
        return PdfService.validate_report(spec(**extra))

    def test_columns_is_a_whole_number_from_one_to_three(self):
        for good in (1, 2, 3):
            self.assertEqual(self.problems(columns=good), [])
        for bad in (0, 4, -1, 2.5, "2", True, [2]):
            self.assertTrue(any("columns must be" in p for p in self.problems(columns=bad)), bad)

    def test_font_size_is_a_number_of_points_within_a_printable_range(self):
        for good in (8, 10.5, 15, 24):
            self.assertEqual(self.problems(fontSize=good), [])
        for bad in (7.9, 24.1, 0, -12, "12", True):
            self.assertTrue(any("fontSize must be" in p for p in self.problems(fontSize=bad)), bad)

    def test_only_the_renderer_that_lays_out_columns_may_be_selected_for_them(self):
        plain = DocumentRequirements.infer("report", spec())
        columned = DocumentRequirements.infer("report", spec(columns=2))
        sized = DocumentRequirements.infer("report", spec(fontSize=14))
        self.assertNotIn("layout_controls", plain.required)
        self.assertNotIn("layout_controls", DocumentRequirements.infer("report", spec(columns=1)).required)
        self.assertIn("layout_controls", columned.required)
        self.assertIn("layout_controls", sized.required)
        supports = {c.name: c.supports for c in capability_registry() if c.available}
        for name, supported in supports.items():
            if name == "reportlab":
                self.assertTrue(columned.required - supported, "ReportLab draws no columns and must not be chosen")


class ReportHtmlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = PdfService(SecurityPolicy(Path(self.tmp.name), Path(self.tmp.name)))
        self.geometry = PageGeometry.from_spec("A4")

    def html(self, blocks, **extra) -> str:
        with patch.object(PdfService, "_font_css", return_value=""):
            return self.service._report_html(
                {"title": "Menu", "blocks": blocks, **extra}, self.service.policy, "IBM Plex Sans", DEFAULT_PALETTE, self.geometry
            )

    def test_a_report_without_columns_has_no_column_wrapper(self):
        body = self.html([{"type": "paragraph", "text": "a"}]).split("<body>")[1]
        self.assertNotIn("columns", body)
        self.assertTrue(body.startswith("<h1>Menu</h1><p>a</p>"))

    def test_columns_flow_the_blocks_after_the_title_and_leave_the_title_full_width(self):
        body = self.html([{"type": "heading", "text": "Breads"}, {"type": "paragraph", "text": "a"}], columns=2).split("<body>")[1]
        self.assertTrue(body.startswith('<h1>Menu</h1><div class="columns columns-2"><h2>Breads</h2><p>a</p></div>'), body[:200])

    def test_a_pagebreak_ends_one_set_of_columns_and_starts_the_next_page(self):
        body = self.html(
            [{"type": "paragraph", "text": "a"}, {"type": "pagebreak"}, {"type": "paragraph", "text": "b"}], columns=3
        ).split("<body>")[1]
        self.assertIn(
            '<div class="columns columns-3"><p>a</p></div><div class="page-break"></div><div class="columns columns-3"><p>b</p></div>',
            body,
        )

    def test_a_leading_pagebreak_is_kept_when_columns_are_on(self):
        body = self.html([{"type": "pagebreak"}, {"type": "paragraph", "text": "b"}], columns=2).split("<body>")[1]
        self.assertIn('<div class="page-break"></div><div class="columns columns-2"><p>b</p></div>', body)

    def stylesheet(self, size: float | None = None) -> str:
        with patch.object(PdfService, "_font_css", return_value=""):
            args = () if size is None else (size,)
            return self.service._html_document("<p>x</p>", "IBM Plex Sans", DEFAULT_PALETTE, self.geometry, "ltr", *args)

    def test_the_default_type_scale_is_unchanged(self):
        css = self.stylesheet()
        for rule in ("font-size:10.5pt;line-height:1.58", "h1{font-size:28pt;", "h2{font-size:18pt;", "h3{font-size:13.5pt;", "font-size:9pt;border:1pt", "figcaption{font-size:8.5pt;"):
            self.assertIn(rule, css)

    def test_one_body_size_scales_every_text_size_in_proportion(self):
        css = self.stylesheet(21)
        for rule in ("font-size:21pt;line-height:1.58", "h1{font-size:56pt;", "h2{font-size:36pt;", "h3{font-size:27pt;", "font-size:18pt;border:1pt", "figcaption{font-size:17pt;"):
            self.assertIn(rule, css)

    def test_null_columns_and_font_size_mean_the_default_not_a_crash(self):
        self.assertEqual(PdfService.validate_report(spec(columns=None, fontSize=None)), [])
        body = self.html([{"type": "paragraph", "text": "a"}], columns=None, fontSize=None)
        self.assertNotIn('class="columns', body)
        self.assertIn("font-size:10.5pt;line-height:1.58", body)

    def test_a_report_font_size_reaches_the_stylesheet(self):
        self.assertIn("font-size:14pt;line-height:1.58", self.html([{"type": "paragraph", "text": "a"}], fontSize=14))

    def test_column_rules_exist_in_the_stylesheet(self):
        css = self.stylesheet()
        self.assertIn(".columns-2{column-count:2}", css)
        self.assertIn(".columns-3{column-count:3}", css)
        self.assertIn(".columns li,.columns figure,.columns table{break-inside:avoid-column}", css)


@unittest.skipUnless(production_available(), "production PDF dependencies/fonts not installed")
class RenderedLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = PdfService(SecurityPolicy(self.root, self.root))

    def render(self, document: dict):
        source = self.root / "report.json"
        source.write_text(json.dumps(document), encoding="utf-8")
        return self.service.generate_report(source, self.root / "out.pdf")

    def words_at(self, *words: str) -> dict[str, tuple[float, float]]:
        """The page position (x, y from the top) at which each given word is drawn."""
        from pypdf import PdfReader

        page = PdfReader(str(self.root / "out.pdf")).pages[0]
        height = float(page.mediabox.height)
        found: dict[str, tuple[float, float]] = {}

        def visit(text, cm, tm, _font_dict, _font_size):
            token = text.strip()
            if token in words and token not in found:
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                found[token] = (x, height - y if y < height else y)

        page.extract_text(visitor_text=visit)
        return found

    def menu(self, columns: int, size: float = 16) -> dict:
        def table(name):
            return {"type": "table", "columns": [name, "Price"], "rows": [[f"{name} {n}", "1.00"] for n in range(1, 3)]}

        return spec(
            columns=columns,
            blocks=[
                {"type": "heading", "text": "Breads"}, table("Loaf"),
                {"type": "heading", "text": "Pastries"}, table("Pastry"),
                {"type": "heading", "text": "Coffee"}, table("Drink"),
                {"type": "heading", "text": "Extras"}, table("Extra"),
            ],
            fontSize=size,
        )

    def test_two_columns_put_later_sections_beside_earlier_ones(self):
        self.assertEqual(self.render(self.menu(2)).pages, 1)
        placed = self.words_at("Breads", "Extras")
        self.assertEqual(set(placed), {"Breads", "Extras"}, placed)
        self.assertGreater(placed["Extras"][0] - placed["Breads"][0], 100, "the last section is not in a second column")

    def test_one_column_stacks_the_same_sections(self):
        self.assertEqual(self.render(self.menu(1, 10.5)).pages, 1)
        placed = self.words_at("Breads", "Extras")
        self.assertEqual(set(placed), {"Breads", "Extras"}, placed)
        self.assertLess(abs(placed["Extras"][0] - placed["Breads"][0]), 5, "a single column drifted sideways")

    def test_a_larger_body_size_takes_more_room(self):
        paragraphs = [{"type": "paragraph", "text": "Naturally leavened bread, baked every morning. " * 12}] * 8
        small = self.render(spec(blocks=paragraphs, fontSize=8)).pages
        large = self.render(spec(blocks=paragraphs, fontSize=24)).pages
        self.assertGreater(large, small)

    def embedded_font_names(self) -> set[str]:
        """Full names of the font programs the page embeds, read from the font files themselves."""
        import io

        from fontTools.ttLib import TTFont
        from pypdf import PdfReader

        names = set()
        for font in PdfReader(str(self.root / "out.pdf")).pages[0]["/Resources"]["/Font"].values():
            font = font.get_object()
            descriptor = font["/DescendantFonts"][0].get_object()["/FontDescriptor"].get_object()
            program = (descriptor.get("/FontFile2") or descriptor["/FontFile3"]).get_object().get_data()
            names.add(next(n.toUnicode() for n in TTFont(io.BytesIO(program))["name"].names if n.nameID == 4))
        return names

    @unittest.skipUnless(installed("DejaVu Serif"), "DejaVu Serif is not installed")
    def test_a_serif_report_embeds_a_regular_and_a_bold_serif_face(self):
        self.render(spec(font="DejaVu Serif", palette=PALETTE))
        self.assertEqual(self.embedded_font_names(), {"DejaVu Serif", "DejaVu Serif Bold"})

if __name__ == "__main__":
    unittest.main()
