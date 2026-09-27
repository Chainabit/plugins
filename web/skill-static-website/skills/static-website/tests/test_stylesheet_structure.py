"""Packaged font declarations must not swallow the stylesheet in CSS parsing."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('scaffold_css_under_test', ROOT / 'scripts/scaffold_site.py')
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

try:
    import tinycss2
except ImportError:
    tinycss2 = None

@unittest.skipUnless(tinycss2, 'CSS parser is installed with the PDF rendering runtime')
class StylesheetStructureTests(unittest.TestCase):
    def test_packaged_font_rules_preserve_the_rest_of_the_stylesheet(self):
        site = MODULE.validate_spec(json.loads(json.dumps(MODULE.TEMPLATES['portfolio'])))[0]['site']
        css = MODULE.render_stylesheet(site)
        rules = tinycss2.parse_stylesheet(css, skip_whitespace=True, skip_comments=True)
        faces = [r for r in rules if r.type == 'at-rule' and r.lower_at_keyword == 'font-face']
        self.assertEqual(len(faces), 4)
        for face in faces:
            declarations = tinycss2.parse_declaration_list(face.content, skip_whitespace=True, skip_comments=True)
            self.assertTrue(all(d.type == 'declaration' for d in declarations), declarations)
            self.assertEqual({d.lower_name for d in declarations}, {'font-family', 'font-style', 'font-weight', 'font-display', 'src'})
        selectors = [tinycss2.serialize(r.prelude).strip() for r in rules if r.type == 'qualified-rule']
        self.assertIn(':root', selectors)
        self.assertIn('body', selectors)
