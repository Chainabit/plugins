from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_FILES = (
    "IBMPlexSans-Regular.woff2",
    "IBMPlexSans-SemiBold.woff2",
    "IBMPlexSansArabic-Regular.woff2",
    "IBMPlexSansArabic-SemiBold.woff2",
)


class StaticWebsiteArtifactContractTests(unittest.TestCase):
    def test_validation_identity_survives_a_producer_edit_after_parsing(self) -> None:
        import contextlib
        import importlib.util
        import io
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            printed = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--template", "landing", "--print-spec"], capture_output=True, text=True, check=True)
            spec = json.loads(printed.stdout)
            spec["site"]["font"] = "Inter"
            spec["site"]["theme"] = "light"
            spec["site"]["palette"] = {"light": {"background":"#FFFFFF", "surface":"#FDFBFF", "ink":"#2D123D", "body":"#4C2C5B", "muted":"#6B4C7A", "rule":"#DEC9EA", "accent":"#6D28D9", "accentInk":"#FFFFFF"}}
            source = root / "spec.json"
            source.write_text(json.dumps(spec), encoding="utf-8")
            built = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--spec", str(source), str(site)], capture_output=True, text=True, check=False)
            self.assertEqual(built.returncode, 0, built.stderr)
            produced = json.loads(built.stdout.strip().splitlines()[-1])
            module_spec = importlib.util.spec_from_file_location("site_byte_identity_validator", ROOT / "scripts/validate_site.py")
            validator = importlib.util.module_from_spec(module_spec)
            sys.modules[module_spec.name] = validator
            module_spec.loader.exec_module(validator)
            check_contract = validator.check_contract

            def edit_after_checks(*args, **kwargs):
                result = check_contract(*args, **kwargs)
                (site / "index.html").write_text("different invalid website", encoding="utf-8")
                return result

            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(validator, "check_contract", side_effect=edit_after_checks), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = validator.main([str(site), "--strict"])
            self.assertEqual(result, 0, stderr.getvalue())
            checked = json.loads(stdout.getvalue().strip().splitlines()[-1])
            self.assertEqual(checked["subject"]["sha256"], produced["output"]["sha256"])
            self.assertEqual(checked["subject"]["bytes"], produced["output"]["bytes"])

    def test_exact_tree_identity_default_override_and_external_asset_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fonts = root / "fonts"
            fonts.mkdir()
            for name in FONT_FILES:
                (fonts / name).write_bytes(b"wOF2unit-test-font")
            env = {**os.environ, "CHAINABIT_ARTIFACT_FONT_DIR": str(fonts)}
            for override, family in ((None, "IBM Plex Sans"), ("IBM Plex Sans Arabic", "IBM Plex Sans Arabic")):
                spec_result = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--template", "portfolio", "--print-spec"], capture_output=True, text=True, check=True)
                spec = json.loads(spec_result.stdout)
                if override:
                    spec["site"]["font"] = override
                source, site = root / f"{family}.json", root / family.replace(" ", "-")
                source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
                built = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--spec", str(source), str(site)], env=env, capture_output=True, text=True, check=False)
                self.assertEqual(built.returncode, 0, built.stderr)
                produced = json.loads(built.stdout.strip().splitlines()[-1])
                checked = subprocess.run([sys.executable, str(ROOT / "scripts/validate_site.py"), str(site), "--strict"], capture_output=True, text=True, check=False)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                validation = json.loads(checked.stdout.strip().splitlines()[-1])
                self.assertEqual(validation["subject"]["sha256"], produced["output"]["sha256"])
                self.assertEqual(validation["checks"]["typography"]["family"], family)
                if override is None:
                    css = (site / "assets/site.css").read_text(encoding="utf-8")
                    self.assertIn("--accent: #327B61", css)
                    self.assertIn("--accent: #70BD9E", css)
                    contract = json.loads((site / ".chainabit-site.json").read_text(encoding="utf-8"))
                    self.assertEqual(contract["branding"]["source"], "chainabit_default")

            spec_result = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--template", "landing", "--print-spec"], capture_output=True, text=True, check=True)
            custom = json.loads(spec_result.stdout)
            custom["site"]["theme"] = "light"
            custom["site"]["font"] = "Inter"
            custom["site"]["palette"] = {
                "light": {
                    "background": "#FFFFFF", "surface": "#FDFBFF", "ink": "#2D123D",
                    "body": "#4C2C5B", "muted": "#6B4C7A", "rule": "#DEC9EA",
                    "accent": "#6D28D9", "accentInk": "#FFFFFF",
                }
            }
            custom_source, custom_site = root / "custom.json", root / "customer-site"
            custom_source.write_text(json.dumps(custom), encoding="utf-8")
            built = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--spec", str(custom_source), str(custom_site)], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(built.returncode, 0, built.stderr)
            css = (custom_site / "assets/site.css").read_text(encoding="utf-8")
            self.assertIn("--accent: #6D28D9", css)
            self.assertNotIn("--accent: #327B61", css)
            self.assertIn('--font-sans: "Inter", sans-serif;', css)
            self.assertNotIn('"IBM Plex Sans Arabic", sans-serif', css)
            self.assertFalse((custom_site / "assets" / "fonts").exists())
            contract = json.loads((custom_site / ".chainabit-site.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["branding"]["source"], "user_override")
            self.assertEqual(contract["branding"]["palettes"], custom["site"]["palette"])
            self.assertEqual(contract["typography"], {"family": "Inter", "source": "user_override"})
            checked = subprocess.run([sys.executable, str(ROOT / "scripts/validate_site.py"), str(custom_site), "--strict"], capture_output=True, text=True, check=False)
            self.assertEqual(checked.returncode, 0, checked.stderr)

            partial = json.loads(json.dumps(custom))
            partial["site"]["palette"] = {"light": {"accent": "#6D28D9"}}
            partial_source = root / "partial.json"
            partial_source.write_text(json.dumps(partial), encoding="utf-8")
            rejected = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--spec", str(partial_source), "--validate-only"], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("must include every role", rejected.stderr)

            font_only = json.loads(json.dumps(custom))
            font_only["site"].pop("palette")
            font_only_source = root / "font-only.json"
            font_only_source.write_text(json.dumps(font_only), encoding="utf-8")
            rejected = subprocess.run([sys.executable, str(ROOT / "scripts/scaffold_site.py"), "--spec", str(font_only_source), "--validate-only"], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("required for a non-Chainabit font override", rejected.stderr)

            site = root / "IBM-Plex-Sans"
            css = site / "assets/site.css"
            css.write_text(css.read_text(encoding="utf-8") + "\n.bad{background:url(https://internal.invalid/secret)}", encoding="utf-8")
            rejected = subprocess.run([sys.executable, str(ROOT / "scripts/validate_site.py"), str(site)], capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("another host", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
