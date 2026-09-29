"""A page count the user asked for is enforced where the pages are counted.

A request for a ten-page book was delivered as thirty-one pages. The renderer
reported the count it wrote and nothing compared it with the request, and the
instructions let a document run past its requested length "because the subject
needed the room". A stated length is a limit as well as a target.

The contract is asymmetric on purpose. Over the request by more than a page is
refused before anything is persisted, because cutting is cheap and the refusal
names the measured length. Short of it is delivered and reported, because
deepening a document takes real time and a run that ends with no file serves
nobody; the instructions bound that revision to one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdf_system.errors import ErrorCode, PdfError  # noqa: E402
from pdf_system.length import assess, refuse_if_over, report, requested_pages  # noqa: E402


def production_available() -> bool:
    try:
        import pypdf  # noqa: F401
        import weasyprint  # noqa: F401
    except Exception:
        return False
    fonts = Path(os.environ.get("CHAINABIT_ARTIFACT_FONT_DIR", ""))
    return fonts.joinpath("IBMPlexSans-Regular.ttf").is_file()


def paged_markdown(pages: int) -> str:
    """A source of exactly ``pages`` pages: a form feed is the explicit page break."""
    return "\f".join(f"# Sayfa {n}\n\nBu, {n}. sayfanın kendi içeriğidir." for n in range(1, pages + 1))


def paged_report(pages: int) -> dict:
    blocks: list[dict] = []
    for number in range(1, pages + 1):
        if number > 1:
            blocks.append({"type": "pagebreak"})
        blocks.append({"type": "paragraph", "text": f"Content of page {number}."})
    return {"title": "Length fixture", "blocks": blocks}


class RequestedPagesTests(unittest.TestCase):
    def test_no_stated_length_is_no_requirement(self):
        self.assertIsNone(requested_pages(None, 1000))
        self.assertIsNone(report(None, 31))

    def test_a_stated_length_is_a_whole_number_of_pages(self):
        for value, expected in ((10, 10), ("10", 10), (" 7 ", 7), ("1", 1), ("1000", 1000)):
            self.assertEqual(requested_pages(value, 1000), expected, value)

    def test_anything_else_is_invalid_input_the_caller_can_fix(self):
        for value in ("", "abc", "0", "-3", "2.5", "1001", "1" * 5000, True, "١٠", object()):
            with self.assertRaises(PdfError, msg=repr(value)) as refused:
                requested_pages(value, 1000)
            self.assertEqual(refused.exception.code, ErrorCode.INVALID_INPUT)

    def test_a_page_either_way_is_within_the_request(self):
        self.assertEqual(
            [assess(10, delivered) for delivered in (8, 9, 10, 11, 12)],
            ["short", "within", "within", "within", "over"],
        )

    def test_a_single_page_request_allows_a_second_page_and_no_third(self):
        self.assertEqual([assess(1, delivered) for delivered in (1, 2, 3)], ["within", "within", "over"])

    def test_the_verdict_a_renderer_prints_names_all_three_numbers(self):
        self.assertEqual(report(10, 8), {"requested": 10, "delivered": 8, "status": "short"})
        self.assertEqual(report(10, 11), {"requested": 10, "delivered": 11, "status": "within"})

    def test_the_refusal_names_the_measured_length_and_the_way_out(self):
        with self.assertRaises(PdfError) as refused:
            refuse_if_over(10, 31)
        self.assertEqual(refused.exception.code, ErrorCode.INVALID_INPUT)
        message = refused.exception.message
        self.assertIn("renders to 31 pages but 10 were requested", message)
        self.assertIn("nothing was written", message)
        self.assertIn("about 32% of its current length", message)
        self.assertIn("render again with the same --pages 10", message)
        self.assertIn("Dropping or raising --pages does not meet the request", message)

    def test_within_short_and_unrequested_are_never_refused(self):
        for requested, delivered in ((10, 8), (10, 9), (10, 11), (None, 500)):
            refuse_if_over(requested, delivered)


@unittest.skipUnless(production_available(), "production PDF dependencies/fonts not installed")
class PageBudgetRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, source: str, *options: str, kind: str = "md") -> subprocess.CompletedProcess:
        script = "md_to_pdf.py" if kind == "md" else "report_pdf.py"
        path = self.root / ("book.md" if kind == "md" else "report.json")
        path.write_text(source, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), str(path), str(self.root / "out.pdf"), *options],
            capture_output=True, text=True, check=False,
        )

    def pages_in(self, output: Path) -> int:
        from pypdf import PdfReader

        return len(PdfReader(str(output)).pages)

    def test_a_ten_page_request_is_not_delivered_as_thirty_one(self):
        rendered = self.render(paged_markdown(31), "--pages", "10", "--lang", "tr")
        self.assertEqual(rendered.returncode, 1, rendered.stdout)
        error = json.loads(rendered.stderr)["error"]
        self.assertEqual(error["code"], "invalid_input")
        self.assertEqual(error["class"], "invalid_user_input")
        self.assertFalse(error["retryable"])
        self.assertIn("renders to 31 pages but 10 were requested", error["message"])
        self.assertFalse((self.root / "out.pdf").exists(), "a refused render left a PDF behind")

    def test_a_refusal_leaves_an_earlier_file_at_the_path_untouched(self):
        (self.root / "out.pdf").write_bytes(b"earlier delivery")
        rendered = self.render(paged_markdown(6), "--pages", "2")
        self.assertEqual(rendered.returncode, 1, rendered.stdout)
        self.assertEqual((self.root / "out.pdf").read_bytes(), b"earlier delivery")

    def test_within_a_page_either_way_is_delivered_and_reported(self):
        for requested in ("4", "5", "6"):
            rendered = self.render(paged_markdown(5), "--pages", requested)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            result = json.loads(rendered.stdout)
            self.assertEqual(result["output"]["pages"], 5)
            self.assertEqual(
                result["length"],
                {"requested": int(requested), "delivered": 5, "status": "within"},
            )
            self.assertEqual(self.pages_in(self.root / "out.pdf"), 5)

    def test_short_by_more_than_a_page_is_delivered_and_reported_not_refused(self):
        rendered = self.render(paged_markdown(5), "--pages", "7")
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertEqual(
            json.loads(rendered.stdout)["length"],
            {"requested": 7, "delivered": 5, "status": "short"},
        )
        self.assertTrue((self.root / "out.pdf").is_file())

    def test_no_stated_length_means_no_verdict_and_no_refusal(self):
        rendered = self.render(paged_markdown(14))
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        result = json.loads(rendered.stdout)
        self.assertEqual(result["output"]["pages"], 14)
        self.assertNotIn("length", result)

    def test_an_unusable_count_is_invalid_input_and_writes_nothing(self):
        for value in ("abc", "0", "-3", "2.5", ""):
            rendered = self.render(paged_markdown(3), "--pages", value)
            self.assertEqual(rendered.returncode, 1, (value, rendered.stdout))
            self.assertEqual(json.loads(rendered.stderr)["error"]["code"], "invalid_input", value)
            self.assertFalse((self.root / "out.pdf").exists(), value)

    def test_a_report_spec_is_held_to_the_same_length(self):
        over = self.render(json.dumps(paged_report(5)), "--pages", "2", kind="report")
        self.assertEqual(over.returncode, 1, over.stdout)
        self.assertIn("renders to 5 pages but 2 were requested", json.loads(over.stderr)["error"]["message"])
        self.assertFalse((self.root / "out.pdf").exists())

        within = self.render(json.dumps(paged_report(5)), "--pages", "5", kind="report")
        self.assertEqual(within.returncode, 0, within.stderr)
        self.assertEqual(
            json.loads(within.stdout)["length"],
            {"requested": 5, "delivered": 5, "status": "within"},
        )


if __name__ == "__main__":
    unittest.main()
