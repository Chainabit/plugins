from __future__ import annotations
import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import (ErrorCode, PdfError, failure_class,
                               renderer_exit_code)
from pdf_system.models import PageGeometry
from pdf_system.verification import verify_pdf
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
def production_dependencies_available():
 try:
  import weasyprint, pypdf  # noqa: F401
  return Path(__import__('os').environ.get('CHAINABIT_ARTIFACT_FONT_DIR','')).joinpath('IBMPlexSans-Regular.ttf').is_file()
 except Exception:return False
class PdfSystemTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.source=self.root/'in'; self.output=self.root/'out'; self.source.mkdir(); self.output.mkdir(); self.policy=SecurityPolicy(self.source,self.output)
 def tearDown(self): self.tmp.cleanup()
 def test_markdown_and_geometry(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  src=self.source/'a.md';src.write_text('Heading\n\nbody');one=self.output/'one.pdf';s=PdfService(self.policy);s.generate_markdown(src,one,title='T');self.assertEqual(verify_pdf(one,self.policy.limits).pages,1)
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
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  src=self.source/'a.md';src.write_text('text'); service=PdfService(self.policy)
  import concurrent.futures
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(lambda n: service.generate_markdown(src,self.output/f'{n}.pdf').pages,range(4)))
  self.assertEqual(len(list(self.output.glob('*.pdf'))),4)
 def test_missing_font_assets_are_typed_runtime_failure(self):
  from unittest.mock import patch
  src=self.source/'unicode.md'; src.write_text('Çağrı Şişli')
  with patch('pdf_system.service.DEFAULT_FONT_DIR', self.root/'missing'):
   with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(src,self.output/'unicode.pdf')
  self.assertIn(e.exception.code,{ErrorCode.DEPENDENCY_UNAVAILABLE,ErrorCode.UNSUPPORTED_CAPABILITY,ErrorCode.FONT_FAILURE})
 def test_diagnosis_explains_capability_selection(self):
  report=PdfService(self.policy).diagnose('markdown','| A | B |\n|---|---|\n|1|2|')
  self.assertIn('rich_markdown', report['requirements'])
  self.assertTrue(all('missing' in b and 'available' in b for b in report['backends']))
 def test_weasyprint_object_stream_unicode_and_exact_hash(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'tr.md';src.write_text((FIXTURES/'turkish.md').read_text(encoding='utf-8'),encoding='utf-8');out=self.output/'tr.pdf'
  result=PdfService(self.policy).generate_markdown(src,out,lang='tr',deterministic=False,quality_profile='professional')
  self.assertGreater(result.pages,0);self.assertEqual(result.sha256,hashlib.sha256(out.read_bytes()).hexdigest())
  self.assertIn(b'/ObjStm',out.read_bytes());self.assertEqual(verify_pdf(out,self.policy.limits).sha256,result.sha256)
 def test_arabic_companion_font_is_embedded_offline(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'ar.md';src.write_text('# Chainabit\n\nمرحبا بالعالم — Türkçe ğüşöçıİĞÜŞÖÇ',encoding='utf-8');out=self.output/'ar.pdf'
  PdfService(self.policy).generate_markdown(src,out,lang='ar',deterministic=False,quality_profile='professional')
  verification=verify_pdf(out,self.policy.limits)
  self.assertTrue(any('ArtifactArabic' in font.replace(' ','') for font in verification.fonts),verification.fonts)
 def test_long_document_and_page_breaks(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'long.md';src.write_text('# Uzun Rapor\n\n'+('\n\n'.join(f'## Bölüm {i}\n'+('ğüşöçıİĞÜŞÖÇ uzun içerik. '*80) for i in range(40))),encoding='utf-8');out=self.output/'long.pdf'
  result=PdfService(self.policy).generate_markdown(src,out,lang='tr',deterministic=False,quality_profile='professional')
  self.assertGreater(result.pages,10);self.assertLessEqual(result.pages,self.policy.limits.max_pages)
 def test_readable_blank_pdf_is_rejected(self):
  try:
   from pypdf import PdfWriter
  except ImportError: self.skipTest('pypdf not installed')
  out=self.output/'blank.pdf';writer=PdfWriter();writer.add_blank_page(width=595,height=842)
  with out.open('wb') as handle:writer.write(handle)
  with self.assertRaises(PdfError) as error:verify_pdf(out,self.policy.limits)
  self.assertEqual(error.exception.code,ErrorCode.VALIDATION_FAILURE)
 def test_html_renamed_pdf_is_rejected(self):
  out=self.output/'spoof.pdf';out.write_bytes(b'<html><body>not a PDF</body></html>')
  with self.assertRaises(PdfError) as error:verify_pdf(out,self.policy.limits)
  self.assertEqual(error.exception.code,ErrorCode.CORRUPTED_OUTPUT)
 def test_typed_error_exit_contract(self):
  invalid=PdfError(ErrorCode.INVALID_INPUT,'bad input');dependency=PdfError(ErrorCode.DEPENDENCY_UNAVAILABLE,'missing')
  self.assertEqual(renderer_exit_code(invalid),1);self.assertEqual(renderer_exit_code(dependency),2)
  self.assertEqual(failure_class(invalid),'invalid_user_input');self.assertEqual(failure_class(dependency),'missing_runtime_dependency')
 def test_cli_success_and_validator_protocols(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'tr.md';src.write_text((FIXTURES/'turkish.md').read_text(encoding='utf-8'),encoding='utf-8');out=self.output/'official.pdf'
  rendered=subprocess.run([sys.executable,str(ROOT/'scripts/md_to_pdf.py'),str(src),str(out),'--lang','tr'],capture_output=True,text=True,check=False)
  self.assertEqual(rendered.returncode,0,rendered.stderr);render_message=json.loads(rendered.stdout)
  self.assertEqual(render_message['schema'],'chainabit.pdf.execution/v1');self.assertEqual(render_message['output']['sha256'],hashlib.sha256(out.read_bytes()).hexdigest());self.assertEqual(render_message['typography']['family'],'IBM Plex Sans')
  validated=subprocess.run([sys.executable,str(ROOT/'scripts/validate_pdf.py'),str(out)],capture_output=True,text=True,check=False)
  self.assertEqual(validated.returncode,0,validated.stderr);validation_message=json.loads(validated.stdout)
  self.assertEqual(validation_message['schema'],'chainabit.pdf.validation/v1');self.assertEqual(validation_message['subject']['sha256'],render_message['output']['sha256']);self.assertTrue(validation_message['fonts'])
 def test_cli_input_rejection_is_exit_one(self):
  src=self.source/'empty.md';src.write_text('');out=self.output/'empty.pdf'
  result=subprocess.run([sys.executable,str(ROOT/'scripts/md_to_pdf.py'),str(src),str(out)],capture_output=True,text=True,check=False)
  self.assertEqual(result.returncode,1);message=json.loads(result.stderr);self.assertEqual(message['error']['class'],'invalid_user_input')
 def test_cli_validator_rejects_parseable_blank_pdf(self):
  try:
   from pypdf import PdfWriter
  except ImportError:self.skipTest('pypdf not installed')
  out=self.output/'blank.pdf';writer=PdfWriter();writer.add_blank_page(width=595,height=842)
  with out.open('wb') as handle:writer.write(handle)
  result=subprocess.run([sys.executable,str(ROOT/'scripts/validate_pdf.py'),str(out)],capture_output=True,text=True,check=False)
  self.assertEqual(result.returncode,1);message=json.loads(result.stderr);self.assertEqual(message['error']['class'],'produced_artifact_rejected')
if __name__=='__main__': unittest.main()
