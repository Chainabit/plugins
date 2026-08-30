from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import ErrorCode, PdfError
from pdf_system.models import PageGeometry
from pdf_system.verification import verify_pdf
class PdfSystemTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.source=self.root/'in'; self.output=self.root/'out'; self.source.mkdir(); self.output.mkdir(); self.policy=SecurityPolicy(self.source,self.output)
 def tearDown(self): self.tmp.cleanup()
 def test_deterministic_markdown_and_geometry(self):
  src=self.source/'a.md';src.write_text('Heading\n\nbody');one=self.output/'one.pdf';two=self.output/'two.pdf';s=PdfService(self.policy);s.generate_markdown(src,one,title='T');s.generate_markdown(src,two,title='T');self.assertEqual(one.read_bytes(),two.read_bytes());self.assertEqual(verify_pdf(one,self.policy.limits).pages,1)
 def test_landscape_and_custom_geometry_validation(self):
  self.assertGreater(PageGeometry.from_spec('A4','landscape').width,PageGeometry.from_spec('A4').width)
  with self.assertRaises(ValueError): PageGeometry.from_spec({'width':-1,'height':4})
 def test_path_traversal_and_active_html_are_rejected(self):
  outside=self.root/'outside.md';outside.write_text('x')
  with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(outside,self.output/'x.pdf')
  self.assertEqual(e.exception.code,ErrorCode.UNSAFE_INPUT)
  src=self.source/'x.md';src.write_text('<script>alert(1)</script>')
  with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(src,self.output/'x.pdf')
  self.assertEqual(e.exception.code,ErrorCode.UNSAFE_INPUT)
 def test_invalid_report_and_concurrent_outputs(self):
  src=self.source/'r.json';src.write_text(json.dumps({'title':'x','blocks':[{'type':'evil'}]}))
  with self.assertRaises(PdfError): PdfService(self.policy).generate_report(src,self.output/'r.pdf')
  src=self.source/'a.md';src.write_text('text'); service=PdfService(self.policy)
  import concurrent.futures
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(lambda n: service.generate_markdown(src,self.output/f'{n}.pdf').pages,range(4)))
  self.assertEqual(len(list(self.output.glob('*.pdf'))),4)
 def test_minimal_backend_rejects_unicode_and_rich_semantics(self):
  src=self.source/'unicode.md'; src.write_text('Çağrı Şişli')
  with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(src,self.output/'unicode.pdf')
  self.assertIn(e.exception.code,{ErrorCode.DEPENDENCY_UNAVAILABLE,ErrorCode.UNSUPPORTED_CAPABILITY,ErrorCode.FONT_FAILURE})
  src.write_text('| A | B |\n|---|---|\n| 1 | 2 |')
  with self.assertRaises(PdfError): PdfService(self.policy).generate_markdown(src,self.output/'table.pdf')
 def test_diagnosis_explains_capability_selection(self):
  report=PdfService(self.policy).diagnose('markdown','| A | B |\n|---|---|\n|1|2|')
  self.assertIn('rich_markdown', report['requirements'])
  self.assertTrue(all('missing' in b and 'available' in b for b in report['backends']))
if __name__=='__main__': unittest.main()
