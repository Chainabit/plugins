from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "static_website_scaffold", ROOT / "scripts/scaffold_site.py"
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
SCAFFOLD = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(SCAFFOLD)
FONT_FILES = (
    "IBMPlexSans-Regular.woff2",
    "IBMPlexSans-SemiBold.woff2",
    "IBMPlexSansArabic-Regular.woff2",
    "IBMPlexSansArabic-SemiBold.woff2",
)


def _tiny_png(root: Path, name: str) -> Path:
    """A minimal real 2x2 PNG, not a text stub — the same byte shape a
    generated or uploaded image actually has, so the copy/digest path is
    exercised against real image bytes."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    width = height = 2
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path = root / name
    path.write_bytes(png)
    return path


class StaticWebsiteImageTests(unittest.TestCase):
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

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/scaffold_site.py"), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _validate(self, site: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_site.py"), str(site), "--strict"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_hero_and_card_images_are_copied_rendered_and_pass_validation(self) -> None:
        _tiny_png(self.root, "hero.png")
        _tiny_png(self.root, "card.png")
        spec = self._base_spec()
        spec["pages"][0]["sections"][0]["image"] = {
            "src": "hero.png",
            "alt": "A red square standing in for a generated chart",
        }
        spec["pages"][0]["sections"].append(
            {
                "type": "features",
                "id": "gallery",
                "heading": "Gallery",
                "items": [
                    {
                        "title": "With a picture",
                        "text": "Has an image.",
                        "image": {"src": "card.png", "alt": "A second red square"},
                    },
                    {"title": "Without a picture", "text": "No image field at all."},
                ],
            }
        )
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        site = self.root / "site"

        built = self._run("--spec", str(spec_path), str(site))
        self.assertEqual(built.returncode, 0, built.stderr)

        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="hero-media"', html)
        self.assertIn('alt="A red square standing in for a generated chart"', html)
        self.assertIn('class="card-media"', html)
        self.assertIn('alt="A second red square"', html)
        # The second card has no image at all — no stray <img> for it.
        self.assertEqual(html.count("<img"), 2)

        images = sorted(p.name for p in (site / "assets" / "images").glob("*"))
        self.assertEqual(len(images), 1, "identical bytes must materialize once")
        self.assertTrue(images[0].endswith("-hero.png"))

        checked = self._validate(site)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        validation = json.loads(checked.stdout.strip().splitlines()[-1])
        self.assertTrue(validation["valid"])

    def test_same_image_referenced_twice_copies_once(self) -> None:
        _tiny_png(self.root, "logo.png")
        spec = self._base_spec()
        spec["pages"][0]["sections"][0]["image"] = {"src": "logo.png", "alt": "Logo"}
        spec["pages"][0]["sections"].append(
            {
                "type": "cards",
                "items": [
                    {"title": "Repeats the logo", "image": {"src": "logo.png", "alt": "Logo again"}},
                ],
            }
        )
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        site = self.root / "site"

        built = self._run("--spec", str(spec_path), str(site))
        self.assertEqual(built.returncode, 0, built.stderr)

        images = list((site / "assets" / "images").glob("*"))
        self.assertEqual(len(images), 1, "the same source file must copy exactly once")

    def test_rebuilding_the_same_spec_is_idempotent(self) -> None:
        _tiny_png(self.root, "hero.png")
        spec = self._base_spec()
        spec["pages"][0]["sections"][0]["image"] = {"src": "hero.png", "alt": "Hero"}
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")

        first = self._run("--spec", str(spec_path), str(self.root / "site-a"))
        second = self._run("--spec", str(spec_path), str(self.root / "site-b"))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_digest = json.loads(first.stdout.strip().splitlines()[-1])["output"]["sha256"]
        second_digest = json.loads(second.stdout.strip().splitlines()[-1])["output"]["sha256"]
        self.assertEqual(first_digest, second_digest)

    def _rejected(self, image: dict) -> subprocess.CompletedProcess:
        spec = self._base_spec()
        spec["pages"][0]["sections"][0]["image"] = image
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        return self._run("--spec", str(spec_path), "--validate-only")

    def test_a_remote_url_is_refused_because_the_sandbox_has_no_network(self) -> None:
        result = self._rejected({"src": "https://cdn.example/x.png", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("is a URL or data URI", result.stderr)

    def test_a_data_uri_is_refused_the_same_way(self) -> None:
        result = self._rejected({"src": "data:image/png;base64,AAAA", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("is a URL or data URI", result.stderr)

    def test_missing_alt_is_refused(self) -> None:
        _tiny_png(self.root, "hero.png")
        result = self._rejected({"src": "hero.png"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("must describe the image", result.stderr)

    def test_a_file_that_does_not_exist_is_refused(self) -> None:
        result = self._rejected({"src": "nope.png", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist next to the spec", result.stderr)

    def test_path_traversal_is_refused(self) -> None:
        result = self._rejected({"src": "../../etc/passwd", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("escapes the directory", result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_a_symlink_cannot_publish_a_file_outside_the_spec_directory(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.png"
            secret.write_bytes(b"not public")
            (self.root / "leak.png").symlink_to(secret)
            spec = self._base_spec()
            spec["pages"][0]["sections"][0]["image"] = {"src": "leak.png", "alt": "x"}
            spec_path = self.root / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            destination = self.root / "site"

            result = self._run("--spec", str(spec_path), str(destination))

        self.assertEqual(result.returncode, 1)
        self.assertIn("uses a symbolic link", result.stderr)
        self.assertFalse(destination.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_a_path_swapped_after_validation_is_rejected_by_the_secure_open(self) -> None:
        image = _tiny_png(self.root, "hero.png")
        spec = self._base_spec()
        spec["pages"][0]["sections"][0]["image"] = {
            "src": image.name,
            "alt": "x",
        }
        checked, errors = SCAFFOLD.validate_spec(spec, str(self.root))
        self.assertFalse(errors, errors.messages)

        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.png"
            secret.write_bytes(b"not public")
            image.unlink()
            image.symlink_to(secret)

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                SCAFFOLD.resolve_image_assets(checked, str(self.root))

    def test_an_absolute_path_is_refused(self) -> None:
        result = self._rejected({"src": "/etc/passwd", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("is absolute", result.stderr)

    def test_an_unsupported_extension_is_refused(self) -> None:
        (self.root / "evil.svg").write_text("<svg onload=alert(1)></svg>", encoding="utf-8")
        result = self._rejected({"src": "evil.svg", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported extension", result.stderr)

    def test_an_oversized_image_is_refused(self) -> None:
        big = self.root / "huge.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (11 * 1024 * 1024))
        result = self._rejected({"src": "huge.png", "alt": "x"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("the limit is", result.stderr)

    def test_too_many_canonical_image_sources_are_refused_before_output(self) -> None:
        spec = self._base_spec()
        pages = []
        image_index = 0
        for page_index in range(2):
            hero_name = f"image-{image_index}.png"
            _tiny_png(self.root, hero_name)
            image_index += 1
            sections = [
                {
                    "type": "hero",
                    "heading": f"Gallery {page_index}",
                    "image": {"src": hero_name, "alt": "x"},
                }
            ]
            for _ in range(11):
                items = []
                for _ in range(9):
                    if image_index >= 129:
                        break
                    name = f"image-{image_index}.png"
                    _tiny_png(self.root, name)
                    image_index += 1
                    items.append(
                        {"title": name, "image": {"src": name, "alt": "x"}}
                    )
                if items:
                    sections.append({"type": "cards", "items": items})
            pages.append(
                {
                    "path": "index.html" if page_index == 0 else "gallery.html",
                    "title": f"Gallery {page_index}",
                    "sections": sections,
                }
            )
        self.assertEqual(image_index, 129)
        spec["pages"] = pages
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        destination = self.root / "site"

        result = self._run("--spec", str(spec_path), str(destination))

        self.assertEqual(result.returncode, 1)
        self.assertIn("129 distinct local files, the limit is 128", result.stderr)
        self.assertFalse(destination.exists())

    def test_aggregate_image_bytes_are_refused_before_output(self) -> None:
        names = []
        for index in range(7):
            path = _tiny_png(self.root, f"large-{index}.png")
            with path.open("r+b") as handle:
                handle.truncate(9 * 1024 * 1024)
            names.append(path.name)
        spec = self._base_spec()
        spec["pages"][0]["sections"][0]["image"] = {
            "src": names[0],
            "alt": "x",
        }
        spec["pages"][0]["sections"].append(
            {
                "type": "cards",
                "items": [
                    {"title": name, "image": {"src": name, "alt": "x"}}
                    for name in names[1:]
                ],
            }
        )
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        destination = self.root / "site"

        result = self._run("--spec", str(spec_path), str(destination))

        self.assertEqual(result.returncode, 1)
        self.assertIn("bytes across distinct local files, the limit is", result.stderr)
        self.assertFalse(destination.exists())

    def test_a_list_item_ignores_an_image_field_by_design(self) -> None:
        """`list` sections stay text-only (render_section never reads a list
        item's image), matching the spec's own documented section shapes;
        supplying one is simply inert rather than an error, exactly like any
        other unexpected key an item might carry."""
        _tiny_png(self.root, "thumb.png")
        spec = self._base_spec()
        spec["pages"][0]["sections"].append(
            {
                "type": "list",
                "items": [{"title": "Entry", "image": {"src": "thumb.png", "alt": "x"}}],
            }
        )
        spec_path = self.root / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        site = self.root / "site"
        built = self._run("--spec", str(spec_path), str(site))
        self.assertEqual(built.returncode, 0, built.stderr)
        html = (site / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<img", html)


if __name__ == "__main__":
    unittest.main()
