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

            site = root / "IBM-Plex-Sans"
            css = site / "assets/site.css"
            css.write_text(css.read_text(encoding="utf-8") + "\n.bad{background:url(https://internal.invalid/secret)}", encoding="utf-8")
            rejected = subprocess.run([sys.executable, str(ROOT / "scripts/validate_site.py"), str(site)], capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("another host", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
