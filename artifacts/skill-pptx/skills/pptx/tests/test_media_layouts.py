"""A deck asked for with a figure in it comes back carrying the figure.

Every other check this skill runs passes on a deck of clean, readable,
well-fitted text — including the deck that was asked for with a hero image and a
chart and has neither. So these tests do not read the generator's own report:
they render a real `.pptx`, open it back up with `python-pptx`, and count the
pictures and chart parts that are actually in the package. The same spec is then
rendered as a PDF, because the two renderers share one layout engine and a spec
that builds a deck must build its PDF.

The refusal side is checked the same way round: a spec that names a picture it
does not have, or writes the figure out as a sentence, must not produce a file at
all.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECK = str(ROOT / "scripts/deck_pptx.py")
DECK_PDF = str(ROOT / "scripts/deck_pdf.py")
VALIDATOR = str(ROOT / "scripts/validate_pptx.py")


def pptx_available() -> bool:
    try:
        import pptx  # noqa: F401

        return True
    except ImportError:
        return False


def pillow_available() -> bool:
    try:
        from PIL import Image  # noqa: F401

        return True
    except ImportError:
        return False


def reportlab_fonts() -> Path | None:
    """reportlab's own bundled TrueType faces, when reportlab is installed."""
    try:
        import reportlab
    except ImportError:
        return None
    fonts = Path(reportlab.__file__).resolve().parent / "fonts"
    return fonts if (fonts / "Vera.ttf").is_file() and (fonts / "VeraBd.ttf").is_file() else None


def write_picture(path: Path, size: tuple[int, int], colour: tuple[int, int, int]) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, colour)
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4],
        outline=(255, 255, 255),
        width=4,
    )
    image.save(path)


#: One spec that exercises every layout that can carry a figure, plus the two
#: ways a chart slide can carry one.
def media_spec() -> dict:
    return {
        "title": "Launch readiness",
        "author": "Operations",
        "slides": [
            {
                "layout": "title-image",
                "title": "Launch readiness",
                "subtitle": "Üç pazar, tek tarih",
                "image": {"path": "media/hero.png", "alt": "A grid over a dark skyline"},
            },
            {
                "layout": "image-content",
                "title": "Where the risk sits",
                "bullets": ["Payment review is open", "İstanbul triage slipped"],
                "image": {"path": "media/diagram.jpg", "alt": "The release pipeline"},
                "caption": "Release pipeline, current state",
            },
            {
                "layout": "image-full",
                "title": "Ready to ship",
                "caption": "İstanbul depot",
                "image": {"path": "media/hero.png", "alt": "A grid over a dark skyline"},
            },
            {
                "layout": "chart",
                "title": "Open items by priority",
                "chart": {
                    "kind": "bar",
                    "categories": ["P1", "P2", "P3"],
                    "series": [
                        {"name": "Open", "values": [3, 7, 4]},
                        {"name": "Closed", "values": [1, 5, 9]},
                    ],
                    "unit": "items",
                },
                "note": "Counted at 09:00 today.",
            },
            {
                "layout": "chart",
                "title": "Share of effort",
                "chart": {
                    "kind": "pie",
                    "categories": ["Localisation", "Security", "Store"],
                    "series": [{"name": "Days", "values": [8, 5, 3]}],
                },
            },
            {
                "layout": "chart",
                "title": "Weekly burndown",
                "chart": {
                    "kind": "line",
                    "categories": ["W1", "W2", "W3", "W4"],
                    "series": [{"name": "Remaining", "values": [22, 17, 11, 4]}],
                    "unit": "items",
                },
            },
            {
                "layout": "chart",
                "title": "Priority mix, as supplied",
                "image": {"path": "media/chart.png", "alt": "Bar chart of priorities"},
            },
            {"layout": "closing", "title": "Questions"},
        ],
    }


class MediaWorkspace(unittest.TestCase):
    """A workspace with the pictures a spec may name, and a spec beside them."""

    def workspace(self, spec: dict) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The spec sits one level down, and a real picture sits above it, so
        # "outside the spec's directory" is a containment failure rather than a
        # missing file.
        outside = Path(temporary.name)
        write_picture(outside / "outside.png", (64, 64), (9, 9, 9))
        root = outside / "work"
        root.mkdir()
        (root / "media").mkdir()
        write_picture(root / "media/hero.png", (1408, 768), (30, 60, 90))
        write_picture(root / "media/chart.png", (1200, 800), (240, 240, 245))
        write_picture(root / "media/diagram.jpg", (900, 900), (200, 220, 210))
        (root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return root

    def build(self, root: Path, *extra: str):
        return subprocess.run(
            [sys.executable, DECK, "spec.json", "deck.pptx", *extra],
            cwd=root, capture_output=True, text=True, check=False,
        )


@unittest.skipUnless(pptx_available() and pillow_available(), "python-pptx or Pillow is not installed")
class EmbeddedMediaTests(MediaWorkspace):
    def test_every_media_layout_embeds_what_it_declares(self) -> None:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        root = self.workspace(media_spec())
        result = self.build(root)
        self.assertEqual(result.returncode, 0, result.stderr)

        presentation = Presentation(str(root / "deck.pptx"))
        self.assertEqual(len(presentation.slides), 8)

        pictures, charts = [], []
        for slide in presentation.slides:
            pictures.append(
                sum(1 for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE)
            )
            charts.append(sum(1 for shape in slide.shapes if shape.has_chart))

        # title-image, image-content, image-full and the picture-backed chart
        # slide each carry exactly one picture; the three native chart slides
        # carry a chart part and no picture.
        self.assertEqual(pictures, [1, 1, 1, 0, 0, 0, 1, 0])
        self.assertEqual(charts, [0, 0, 0, 1, 1, 1, 0, 0])
        self.assertEqual(sum(pictures), 4)
        self.assertEqual(sum(charts), 3)

    def test_a_native_chart_keeps_its_numbers_and_its_categories(self) -> None:
        from pptx import Presentation
        from pptx.enum.chart import XL_CHART_TYPE

        root = self.workspace(media_spec())
        self.assertEqual(self.build(root).returncode, 0)

        presentation = Presentation(str(root / "deck.pptx"))
        bar = next(
            shape.chart for shape in presentation.slides[3].shapes if shape.has_chart
        )
        self.assertEqual(bar.chart_type, XL_CHART_TYPE.COLUMN_CLUSTERED)
        self.assertEqual(list(bar.plots[0].categories), ["P1", "P2", "P3"])
        self.assertEqual(
            [(series.name, list(series.values)) for series in bar.series],
            [("Open", [3.0, 7.0, 4.0]), ("Closed", [1.0, 5.0, 9.0])],
        )
        # The numbers are printed, because colour alone is not a label.
        self.assertTrue(bar.plots[0].has_data_labels)

        pie = next(
            shape.chart for shape in presentation.slides[4].shapes if shape.has_chart
        )
        self.assertEqual(pie.chart_type, XL_CHART_TYPE.PIE)
        line = next(
            shape.chart for shape in presentation.slides[5].shapes if shape.has_chart
        )
        self.assertEqual(line.chart_type, XL_CHART_TYPE.LINE_MARKERS)

    def test_a_picture_carries_its_description_and_is_not_stretched(self) -> None:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        root = self.workspace(media_spec())
        self.assertEqual(self.build(root).returncode, 0)

        presentation = Presentation(str(root / "deck.pptx"))
        hero = next(
            shape for shape in presentation.slides[0].shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        self.assertEqual(
            hero._element._nvXxPr.cNvPr.get("descr"), "A grid over a dark skyline"
        )
        # A hero fills its box by cropping, never by distorting: the visible
        # aspect ratio after the crop is the box's, and the source is 1408x768.
        visible = (1408 * (1 - hero.crop_left - hero.crop_right)) / (
            768 * (1 - hero.crop_top - hero.crop_bottom)
        )
        self.assertAlmostEqual(visible, hero.width / hero.height, places=2)

        # A diagram is letterboxed instead, so none of the figure is removed.
        diagram = next(
            shape for shape in presentation.slides[1].shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        self.assertEqual(
            (diagram.crop_left, diagram.crop_right, diagram.crop_top, diagram.crop_bottom),
            (0.0, 0.0, 0.0, 0.0),
        )
        self.assertAlmostEqual(diagram.width / diagram.height, 1.0, places=2)

    def test_no_slide_carries_an_unresolved_stand_in(self) -> None:
        from pptx import Presentation

        root = self.workspace(media_spec())
        self.assertEqual(self.build(root).returncode, 0)

        presentation = Presentation(str(root / "deck.pptx"))
        text = "\n".join(
            shape.text_frame.text
            for slide in presentation.slides
            for shape in slide.shapes
            if shape.has_text_frame
        )
        for stand_in in ("inserted here", "goes here", "placeholder", "TODO", "TBD"):
            self.assertNotIn(stand_in.casefold(), text.casefold())


@unittest.skipUnless(pptx_available() and pillow_available(), "python-pptx or Pillow is not installed")
class ValidatorMediaInventoryTests(MediaWorkspace):
    def validate(self, deck: Path, cwd: Path):
        return subprocess.run(
            [sys.executable, VALIDATOR, str(deck)],
            cwd=cwd, capture_output=True, text=True, check=False,
        )

    def test_the_validator_counts_what_the_deck_embeds(self) -> None:
        root = self.workspace(media_spec())
        self.assertEqual(self.build(root).returncode, 0)

        result = self.validate(root / "deck.pptx", root)
        self.assertEqual(result.returncode, 0, result.stderr)
        frame = json.loads(result.stdout.strip().splitlines()[-1])

        self.assertIn("media_inventory", frame["checks"])
        self.assertEqual(frame["subject"]["pictures"], 4)
        self.assertEqual(frame["subject"]["charts"], 3)
        self.assertEqual(
            [(entry["pictures"], entry["charts"]) for entry in frame["subject"]["slideMedia"]],
            [(1, 0), (1, 0), (1, 0), (0, 1), (0, 1), (0, 1), (1, 0), (0, 0)],
        )
        self.assertIn("4 embedded picture(s), 3 chart(s)", result.stdout)

    def test_a_text_only_deck_reports_no_media_rather_than_omitting_the_count(self) -> None:
        root = self.workspace(
            {
                "title": "Text only",
                "slides": [
                    {"layout": "title", "title": "Text only"},
                    {"layout": "content", "title": "Findings", "bullets": ["Volume grew"]},
                ],
            }
        )
        self.assertEqual(self.build(root).returncode, 0)

        frame = json.loads(
            self.validate(root / "deck.pptx", root).stdout.strip().splitlines()[-1]
        )
        self.assertEqual(frame["subject"]["pictures"], 0)
        self.assertEqual(frame["subject"]["charts"], 0)
        self.assertEqual(
            [entry["slide"] for entry in frame["subject"]["slideMedia"]], [1, 2]
        )


@unittest.skipUnless(pillow_available(), "Pillow is not installed")
class MediaRefusalTests(MediaWorkspace):
    """A slide may not declare a figure it does not carry, and cannot fake one.

    Each of these renders nothing. The spec check runs before a byte is written,
    so a refused spec leaves no half-built deck for a caller to deliver.
    """

    def refuse(self, slides: list) -> str:
        root = self.workspace({"title": "Refused", "slides": slides})
        result = self.build(root, "--validate-only")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertFalse((root / "deck.pptx").exists())
        return result.stderr

    def test_text_that_stands_in_for_a_figure_is_refused(self) -> None:
        stderr = self.refuse(
            [
                {
                    "layout": "content",
                    "title": "Milestones",
                    "bullets": [
                        "Volume grew 18%",
                        "(Chart inserted here by script)",
                        "TODO: confirm the second market",
                    ],
                }
            ]
        )
        self.assertIn("slides[0].bullets[1]", stderr)
        self.assertIn("is a stand-in, not content", stderr)
        self.assertIn("slides[0].bullets[2]", stderr)

    def test_prose_that_merely_mentions_a_chart_is_not_refused(self) -> None:
        root = self.workspace(
            {
                "title": "Fine",
                "slides": [
                    {
                        "layout": "content",
                        "title": "Volume",
                        "bullets": ["Revenue grew 18% (see the chart on slide 4)"],
                    }
                ],
            }
        )
        result = self.build(root, "--validate-only")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_layout_that_shows_a_picture_requires_one(self) -> None:
        for layout in ("title-image", "image-content", "image-full"):
            with self.subTest(layout=layout):
                slide = {"layout": layout, "title": "No picture"}
                if layout == "image-content":
                    slide["bullets"] = ["A point"]
                stderr = self.refuse([slide])
                self.assertIn(f"slides[0].image: required for a {layout} slide", stderr)

    def test_a_chart_slide_requires_data_or_a_picture_and_never_both(self) -> None:
        stderr = self.refuse([{"layout": "chart", "title": "Nothing to plot"}])
        self.assertIn("slides[0].chart: required for a chart slide", stderr)

        stderr = self.refuse(
            [
                {
                    "layout": "chart",
                    "title": "Both",
                    "chart": {
                        "kind": "bar",
                        "categories": ["A"],
                        "series": [{"name": "x", "values": [1]}],
                    },
                    "image": {"path": "media/chart.png", "alt": "A bar chart"},
                }
            ]
        )
        self.assertIn("never both", stderr)

    def test_a_picture_must_be_a_local_file_inside_the_specs_own_directory(self) -> None:
        cases = {
            "https://example.invalid/hero.png": "is a URL",
            "data:image/png;base64,AAAA": "is a URL",
            "../outside.png": "outside the spec's own directory",
            "/absolute/hero.png": "must be relative to the spec's own directory",
            "media/absent.png": "does not exist next to the spec",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                stderr = self.refuse(
                    [{"layout": "image-full", "title": "Picture", "image": {"path": path, "alt": "x"}}]
                )
                self.assertIn(expected, stderr)

    def test_a_format_a_slide_cannot_embed_is_named_rather_than_left_to_fail(self) -> None:
        root = self.workspace({"title": "WebP", "slides": []})
        write_picture(root / "media/shot.webp", (64, 64), (1, 2, 3))
        (root / "spec.json").write_text(
            json.dumps(
                {
                    "title": "WebP",
                    "slides": [
                        {
                            "layout": "image-full",
                            "title": "Picture",
                            "image": {"path": "media/shot.webp", "alt": "x"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = self.build(root, "--validate-only")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is not PNG, JPEG or GIF", result.stderr)

    def test_a_series_plots_one_value_per_category(self) -> None:
        stderr = self.refuse(
            [
                {
                    "layout": "chart",
                    "title": "Ragged",
                    "chart": {
                        "kind": "line",
                        "categories": ["A", "B", "C"],
                        "series": [{"name": "x", "values": [1, 2]}],
                    },
                }
            ]
        )
        self.assertIn("2 values for 3 categories", stderr)

    def test_a_pie_takes_one_series(self) -> None:
        stderr = self.refuse(
            [
                {
                    "layout": "chart",
                    "title": "Pie",
                    "chart": {
                        "kind": "pie",
                        "categories": ["A", "B"],
                        "series": [
                            {"name": "x", "values": [1, 2]},
                            {"name": "y", "values": [3, 4]},
                        ],
                    },
                }
            ]
        )
        self.assertIn("the limit for a pie chart is 1", stderr)


@unittest.skipUnless(pillow_available() and reportlab_fonts(), "Pillow or reportlab is not installed")
class MediaPdfParityTests(MediaWorkspace):
    """One spec, two renderers. A spec that builds a deck builds its PDF."""

    def test_the_same_media_spec_renders_as_a_pdf(self) -> None:
        root = self.workspace(media_spec())
        fonts = root / "fonts"
        fonts.mkdir()
        shutil.copyfile(reportlab_fonts() / "Vera.ttf", fonts / "IBMPlexSans-Regular.ttf")
        shutil.copyfile(reportlab_fonts() / "VeraBd.ttf", fonts / "IBMPlexSans-SemiBold.ttf")
        environment = {**os.environ, "CHAINABIT_ARTIFACT_FONT_DIR": str(fonts)}
        environment.pop("CHAINABIT_ARTIFACT_FONT_FAMILY", None)

        result = subprocess.run(
            [sys.executable, DECK_PDF, "spec.json", "deck.pdf"],
            cwd=root, env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        produced = (root / "deck.pdf").read_bytes()
        self.assertTrue(produced.startswith(b"%PDF-"))
        frame = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(frame["generator"], "skill-pptx.deck_pdf")
        self.assertEqual(frame["output"]["bytes"], len(produced))
        # The pictures are embedded, not referenced: a PDF that merely named
        # them would render blank wherever it is opened.
        self.assertIn(b"/Image", produced)

    def test_a_spec_refused_for_the_deck_is_refused_for_the_pdf(self) -> None:
        root = self.workspace(
            {
                "title": "Refused",
                "slides": [{"layout": "image-full", "title": "No picture"}],
            }
        )
        for script in (DECK, DECK_PDF):
            with self.subTest(script=Path(script).name):
                output = "deck.pptx" if script == DECK else "deck.pdf"
                result = subprocess.run(
                    [sys.executable, script, "spec.json", output],
                    cwd=root, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("slides[0].image: required", result.stderr)
                self.assertFalse((root / output).exists())


if __name__ == "__main__":
    unittest.main()
