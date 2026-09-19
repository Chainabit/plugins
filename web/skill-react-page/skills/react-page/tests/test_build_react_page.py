from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_react_page.py"
FONT_FILES = (
    "IBMPlexSans-Regular.woff2",
    "IBMPlexSans-SemiBold.woff2",
    "IBMPlexSansArabic-Regular.woff2",
    "IBMPlexSansArabic-SemiBold.woff2",
)

spec = importlib.util.spec_from_file_location("build_react_page", SCRIPT)
assert spec and spec.loader
page = importlib.util.module_from_spec(spec)
spec.loader.exec_module(page)

# A stand-in for the bundler: it honours only what the build reads back, so the
# pipeline around it (mount, page assembly, checks, evidence) is exercised in
# any environment, with or without the real toolchain.
FAKE_ESBUILD = f"""#!{sys.executable}
import os, sys
if os.environ.get("FAKE_ESBUILD_FAIL"):
    sys.stderr.write("app/App.jsx:3:9: ERROR: Unexpected \\"}}\\"\\n")
    raise SystemExit(1)
out = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--outfile=")][0]
open(out, "w", encoding="utf-8").write(os.environ.get("FAKE_ESBUILD_JS", 'console.log("ok")'))
css = os.environ.get("FAKE_ESBUILD_CSS")
if css:
    open(out[:-3] + ".css", "w", encoding="utf-8").write(css)
"""


def make_toolchain(root: Path) -> Path:
    modules = root / "node_modules"
    (modules / ".bin").mkdir(parents=True)
    binary = modules / ".bin" / "esbuild"
    binary.write_text(FAKE_ESBUILD, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    for package, version in (("react", "19.0.0"), ("react-dom", "19.0.0"), ("esbuild", "0.25.0")):
        (modules / package).mkdir()
        (modules / package / "package.json").write_text(json.dumps({"name": package, "version": version}), encoding="utf-8")
    return root


def make_fonts(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in FONT_FILES:
        (root / name).write_bytes(b"wOF2unit-test-font")
    return root


class Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.toolchain = make_toolchain(self.root / "toolchain")
        self.fonts = make_fonts(self.root / "fonts")
        (self.root / "app").mkdir()
        self.entry = self.root / "app" / "App.jsx"
        self.entry.write_text("export default function App() { return null; }\n", encoding="utf-8")

    def run_cli(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "CHAINABIT_WEB_TOOLCHAIN_DIR": str(self.toolchain),
            "CHAINABIT_ARTIFACT_FONT_DIR": str(self.fonts),
            **env,
        }
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], cwd=self.root, env=environment, capture_output=True, text=True, check=False
        )

    def build(self, *extra: str, **env: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "--entry", str(self.entry), "--out", str(self.root / "page" / "index.html"),
            "--lang", "tr", "--title", "Sunum", *extra, **env,
        )


class InputErrorTests(Workspace):
    def test_bad_input_exits_1_and_names_the_argument(self) -> None:
        cases = {
            "--entry": ["--entry", str(self.root / "missing.jsx"), "--out", "p.html", "--lang", "en", "--title", "t"],
            "--out": ["--entry", str(self.entry), "--out", "p.txt", "--lang", "en", "--title", "t"],
            "--lang": ["--entry", str(self.entry), "--out", "p.html", "--lang", "not a tag", "--title", "t"],
            "--title": ["--entry", str(self.entry), "--out", "p.html", "--lang", "en", "--title", " "],
        }
        for name, argv in cases.items():
            with self.subTest(name):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(name, result.stderr)

    def test_a_missing_required_argument_is_an_input_error_not_a_missing_asset(self) -> None:
        self.assertEqual(self.run_cli("--entry", str(self.entry)).returncode, 1)


class RuntimeAssetTests(Workspace):
    def test_a_missing_toolchain_exits_2_and_forbids_a_cdn_fallback(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        result = self.build(CHAINABIT_WEB_TOOLCHAIN_DIR=str(empty))
        self.assertEqual(result.returncode, 2)
        self.assertIn("web.toolchain", result.stderr)
        self.assertIn("CDN", result.stderr)

    def test_missing_fonts_exit_2_unless_the_system_stack_was_asked_for(self) -> None:
        empty = self.root / "no-fonts"
        empty.mkdir()
        missing = self.build(CHAINABIT_ARTIFACT_FONT_DIR=str(empty))
        self.assertEqual(missing.returncode, 2)
        self.assertIn("fonts.ibm-plex", missing.stderr)
        self.assertEqual(self.build("--font", "none", CHAINABIT_ARTIFACT_FONT_DIR=str(empty)).returncode, 0)


class PipelineTests(Workspace):
    def test_builds_one_self_contained_file_and_reports_its_exact_identity(self) -> None:
        result = self.build(FAKE_ESBUILD_JS="console.log(1)", FAKE_ESBUILD_CSS="main{color:#111}")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout.strip().splitlines()[-1])
        written = (self.root / "page" / "index.html").read_bytes()
        self.assertEqual(record["protocol"], "chainabit.react-page.build/v1")
        self.assertEqual(record["output"]["bytes"], len(written))
        self.assertEqual(record["output"]["sha256"], __import__("hashlib").sha256(written).hexdigest())
        self.assertEqual(record["toolchain"], {"react": "19.0.0", "react-dom": "19.0.0", "esbuild": "0.25.0"})
        text = written.decode("utf-8")
        self.assertIn('<html lang="tr" dir="ltr">', text)
        self.assertIn("<title>Sunum</title>", text)
        self.assertIn("main{color:#111}", text)
        self.assertEqual(script_elements(text), 1)
        self.assertNotIn("<script src", text)
        self.assertEqual(sorted(p.name for p in (self.root / "page").iterdir()), ["index.html"])

    def test_embeds_ibm_plex_by_default_and_the_system_stack_on_request(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        embedded = (self.root / "page" / "index.html").read_text(encoding="utf-8")
        self.assertIn('font-family:"IBM Plex Sans"', embedded)
        self.assertIn("data:font/woff2;base64,", embedded)
        self.assertNotIn("IBM Plex Sans Arabic", embedded)
        self.assertEqual(self.build("--font", "none").returncode, 0)
        plain = (self.root / "page" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("@font-face", plain)
        self.assertIn("system-ui", plain)

    def test_right_to_left_languages_get_dir_and_the_arabic_companion_face(self) -> None:
        result = self.run_cli(
            "--entry", str(self.entry), "--out", str(self.root / "ar.html"), "--lang", "ar-EG", "--title", "عرض"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.root / "ar.html").read_text(encoding="utf-8")
        self.assertIn('<html lang="ar-EG" dir="rtl">', text)
        self.assertIn('font-family:"IBM Plex Sans Arabic"', text)

    def test_script_terminators_in_the_bundle_cannot_close_the_element(self) -> None:
        result = self.build(FAKE_ESBUILD_JS='x="</script><!--";')
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.root / "page" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(text.count("</script"), 1)
        self.assertNotIn("<!--", text)

    def test_a_bundle_failure_exits_3_with_the_bundlers_message_and_writes_nothing(self) -> None:
        result = self.build(FAKE_ESBUILD_FAIL="1")
        self.assertEqual(result.returncode, 3)
        self.assertIn("app/App.jsx:3:9", result.stderr)
        self.assertFalse((self.root / "page").exists())

    def test_a_remote_stylesheet_reference_fails_the_page_checks(self) -> None:
        result = self.build(FAKE_ESBUILD_CSS='@import url("https://fonts.example/x.css");')
        self.assertEqual(result.returncode, 3)
        self.assertIn("outside the file", result.stderr)
        self.assertFalse((self.root / "page").exists())


class AuditTests(unittest.TestCase):
    def test_flags_what_a_page_loads_from_outside_itself(self) -> None:
        document = (
            '<!doctype html><html lang="en"><head><base href="/x/">'
            '<link rel="stylesheet" href="https://cdn.example/a.css"></head><body>'
            '<script src="//cdn.example/react.js"></script><script>1</script>'
            '<img src="data:image/png;base64,AA"><iframe src="https://e.example"></iframe></body></html>'
        )
        findings = page.audit(document, "")
        joined = "\n".join(findings)
        for expected in ("<base>", "<link href>", "<script src>", "<iframe src>", "no alt", "expected one inline <script>"):
            self.assertIn(expected, joined)

    def test_accepts_data_uris_and_in_page_anchors(self) -> None:
        document = (
            '<!doctype html><html lang="en"><body><script>1</script>'
            '<img alt="" src="data:image/png;base64,AA"><a href="#top">top</a></body></html>'
        )
        self.assertEqual(page.audit(document, ""), [])

    def test_requires_a_language(self) -> None:
        self.assertIn("no lang", "\n".join(page.audit("<!doctype html><html><body><script>1</script></body></html>", "")))

    def test_css_references(self) -> None:
        self.assertEqual(page.css_remote_references("a{background:url(data:image/png;base64,AA)}b{background:url(#x)}"), [])
        self.assertEqual(len(page.css_remote_references('a{background:url("https://x.example/i.png")}')), 1)
        self.assertEqual(len(page.css_remote_references("@import 'x.css';")), 1)

    def test_language_tags_and_direction(self) -> None:
        for good in ("en", "tr", "ar-EG", "zh-Hant-TW", "fil"):
            self.assertTrue(page.valid_language_tag(good), good)
        for bad in ("", "e", "en_US", "en--US", "not a tag", "en-" + "x" * 9, "1n"):
            self.assertFalse(page.valid_language_tag(bad), bad)
        self.assertEqual([page.text_direction(t) for t in ("en", "ar-EG", "he", "fa-IR", "tr")], ["ltr", "rtl", "rtl", "rtl", "ltr"])


def script_elements(text: str) -> int:
    audit = page.PageAudit()
    audit.feed(text)
    return audit.scripts


REAL_TOOLCHAIN = Path(os.environ.get("CHAINABIT_WEB_TOOLCHAIN_DIR") or page.TOOLCHAIN_DEFAULT)


@unittest.skipUnless((REAL_TOOLCHAIN / "node_modules/.bin/esbuild").is_file(), "the web toolchain is not installed")
class RealToolchainTests(unittest.TestCase):
    def test_builds_a_working_react_page_with_components_css_and_an_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "app" / "App.jsx").write_text(
                "import { useState } from 'react';\n"
                "import Slide from './Slide.jsx';\n"
                "import './styles.css';\n"
                "import logo from './logo.svg';\n"
                "export default function App() {\n"
                "  const [n, setN] = useState(0);\n"
                "  return <main><Slide n={n} /><img alt='Logo' src={logo} />"
                "<button onClick={() => setN(n + 1)}>Sonraki</button></main>;\n"
                "}\n",
                encoding="utf-8",
            )
            (root / "app" / "Slide.jsx").write_text("export default ({ n }) => <h1>Merhaba çşğıöü {n}</h1>;\n", encoding="utf-8")
            (root / "app" / "styles.css").write_text("main{padding:2rem}\n", encoding="utf-8")
            (root / "app" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--entry", "app/App.jsx", "--out", "page/index.html", "--lang", "tr",
                 "--title", "Gerçek", "--font", "none"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (root / "page" / "index.html").read_text(encoding="utf-8")
            self.assertIn("react.transitional.element", text)  # React itself is inlined
            self.assertIn("Merhaba çşğıöü", text)
            self.assertIn("main{padding:2rem}", text)
            self.assertIn("data:image/svg+xml", text)
            # Counted as elements, not substrings: React DOM's own text mentions "<script".
            self.assertEqual(script_elements(text), 1)
            self.assertEqual(page.audit(text, ""), [])


if __name__ == "__main__":
    unittest.main()
