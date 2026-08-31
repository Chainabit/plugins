from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dependency_available() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(dependency_available(), "openpyxl is not installed")
class XlsxArtifactContractTests(unittest.TestCase):
    def test_default_and_user_override_are_validated(self) -> None:
        for requested, expected in ((None, "IBM Plex Sans"), ("Avenir Next", "Avenir Next")):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec = {"sheets": [{"name": "Türkçe", "columns": [{"header": "Şehir", "type": "text"}], "rows": [["Şanlıurfa"], ["مرحبا بالعالم"]]}]}
                if requested is not None:
                    spec["font"] = requested
                source, output = root / "spec.json", root / "book.xlsx"
                source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
                built = subprocess.run([sys.executable, str(ROOT / "scripts/build_xlsx.py"), str(source), str(output)], capture_output=True, text=True, check=False)
                self.assertEqual(built.returncode, 0, built.stderr)
                produced = json.loads(built.stdout.strip().splitlines()[-1])
                self.assertEqual(produced["output"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
                checked = subprocess.run([sys.executable, str(ROOT / "scripts/validate_xlsx.py"), str(output)], capture_output=True, text=True, check=False)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                validation = json.loads(checked.stdout.strip().splitlines()[-1])
                self.assertEqual(validation["subject"]["sha256"], produced["output"]["sha256"])
                self.assertEqual(validation["typography"]["family"], expected)
                self.assertEqual(validation["typography"]["fallbacks"], ["IBM Plex Sans Arabic"])


if __name__ == "__main__":
    unittest.main()
