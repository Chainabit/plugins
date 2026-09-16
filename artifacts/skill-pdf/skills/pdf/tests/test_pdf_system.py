from __future__ import annotations
import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import (ErrorCode, PdfError, failure_class,
                               renderer_exit_code)
from pdf_system.models import PageGeometry
from pdf_system.verification import verify_pdf
UNICODE_MARKDOWN = '# Unicode Rendering Coverage\n\nLatin Extended-A: **ğüşöçıİĞÜŞÖÇ**.'
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
 def test_html_document_page_rule_reflects_the_resolved_geometry(self):
  """The @page rule is built from the caller's geometry, not a second hardcoded default.

  `_html_document` used to hardcode `@page{size:A4;margin:58pt 54pt 54pt;`, a
  value that matched neither `PageGeometry`'s own default margin (54/50/48/50,
  the one `references/typography.md` documents) nor anything a caller could
  override -- a custom page size was patched in after the fact by a fragile
  string replacement, and a custom margin was never applied at all.
  """
  from unittest.mock import patch
  from pdf_system.service import DEFAULT_PALETTE
  default_geometry=PageGeometry.from_spec('A4','portrait')
  self.assertEqual(default_geometry.margin,(54.0,50.0,48.0,50.0))
  custom_geometry=PageGeometry.from_spec({'width':300,'height':500},'portrait',{'top':10,'right':20,'bottom':30,'left':40})
  service=PdfService(self.policy)
  with patch.object(PdfService,'_font_css',return_value=''):
   document=service._html_document('<p>x</p>','IBM Plex Sans',DEFAULT_PALETTE,True,custom_geometry)
  self.assertIn('@page{size:300.00pt 500.00pt;margin:10.00pt 20.00pt 30.00pt 40.00pt',document)
 def test_geometry_is_resolved_before_html_is_built_and_carries_through(self):
  """A regression guard that needs no renderer: only string assembly and plumbing."""
  from unittest.mock import patch
  from types import SimpleNamespace
  captured={}
  def fake_html_document(self,body,font,palette,show_footer,geometry,direction="ltr"):
   captured['geometry']=geometry
   return f'<html dir="{direction}"><style>@page{{size:{geometry.width:.2f}pt {geometry.height:.2f}pt;margin:{geometry.margin[0]:.2f}pt {geometry.margin[1]:.2f}pt {geometry.margin[2]:.2f}pt {geometry.margin[3]:.2f}pt}}</style></html>'
  def fake_render(document,geometry,metadata,destination,policy):
   captured['render_geometry']=geometry; destination.write_bytes(b'%PDF-1.4 fake')
  src=self.source/'m.md';src.write_text('Body text')
  backend=SimpleNamespace(capabilities=SimpleNamespace(name='weasyprint'),render=fake_render)
  service=PdfService(self.policy, resolver=SimpleNamespace(resolve=lambda *_: (backend, None)))
  with patch.object(PdfService,'_html_document',fake_html_document), \
       patch('pdf_system.service.verify_pdf',return_value=SimpleNamespace(bytes=1,pages=1,version='1.4',sha256='s',mime_type='application/pdf',warnings=())):
   service.generate_markdown(src,self.output/'m.pdf',margin={'top':10,'right':20,'bottom':30,'left':40})
  self.assertEqual(captured['geometry'].margin,(10.0,20.0,30.0,40.0))
  self.assertIs(captured['render_geometry'],captured['geometry'])
 def test_larger_margin_forces_more_pages(self):
  """An end-to-end proof that a requested margin shrinks the real printable area."""
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  body='\n\n'.join(f'Paragraph {i}. ' + 'word '*40 for i in range(24))
  src=self.source/'m.md';src.write_text('# Report\n\n'+body)
  narrow=PdfService(self.policy).generate_markdown(src,self.output/'narrow-margin.pdf',margin=10)
  wide=PdfService(self.policy).generate_markdown(src,self.output/'wide-margin.pdf',margin=200)
  self.assertGreater(wide.pages,narrow.pages)
 def test_path_traversal_and_active_html_are_rejected(self):
  outside=self.root/'outside.md';outside.write_text('x')
  with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(outside,self.output/'x.pdf')
  self.assertEqual(e.exception.code,ErrorCode.UNSAFE_INPUT)
  src=self.source/'x.md';src.write_text('<script>alert(1)</script>')
  with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(src,self.output/'x.pdf')
  self.assertEqual(e.exception.code,ErrorCode.UNSAFE_INPUT)
 def test_report_image_caption_is_visible_not_only_alt_text(self):
  """A report image block's `caption` used to reach only the <img alt>
  attribute -- accessibility metadata no renderer paints onto the page -- so
  a caller-requested caption never appeared in the delivered PDF. It must
  now show up as real body text, in a <figcaption> WeasyPrint actually
  renders, distinct from the alt attribute it still also carries."""
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  from PIL import Image
  img=self.source/'photo.png';Image.new('RGB',(40,20),color=(10,20,30)).save(img)
  src=self.source/'r.json';src.write_text(json.dumps({'title':'HQ','blocks':[{'type':'image','path':'photo.png','caption':'Our new HQ, opening 2027'}]}))
  out=self.output/'r.pdf';PdfService(self.policy).generate_report(src,out)
  from pypdf import PdfReader
  text=PdfReader(str(out)).pages[0].extract_text()
  self.assertIn('Our new HQ, opening 2027',text)
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
  src=self.source/'unicode.md'; src.write_text('Zoë Faßbinder')
  with patch('pdf_system.service.DEFAULT_FONT_DIR', self.root/'missing'):
   with self.assertRaises(PdfError) as e: PdfService(self.policy).generate_markdown(src,self.output/'unicode.pdf')
  self.assertIn(e.exception.code,{ErrorCode.DEPENDENCY_UNAVAILABLE,ErrorCode.UNSUPPORTED_CAPABILITY,ErrorCode.FONT_FAILURE})
 def test_diagnosis_explains_capability_selection(self):
  report=PdfService(self.policy).diagnose('markdown','| A | B |\n|---|---|\n|1|2|')
  self.assertIn('rich_markdown', report['requirements'])
  self.assertTrue(all('missing' in b and 'available' in b for b in report['backends']))
 def test_default_and_complete_custom_palette_resolution(self):
  from pdf_system.service import DEFAULT_PALETTE, resolve_palette, validate_palette
  self.assertEqual(DEFAULT_PALETTE['accent'], '#327B61')
  custom={'background':'#FFFFFF','surface':'#FDFBFF','ink':'#2D123D','body':'#4C2C5B','muted':'#6B4C7A','rule':'#DEC9EA','accent':'#6D28D9','accentInk':'#FFFFFF'}
  self.assertEqual(validate_palette(custom), [])
  self.assertEqual(resolve_palette(custom)['accent'], '#6D28D9')
  self.assertEqual(resolve_palette(None)['accent'], '#327B61')
  self.assertIn('must include every role', validate_palette({'accent':'#6D28D9'})[0])
  font_only={'title':'Customer report','font':'Avenir Next','blocks':[{'type':'paragraph','text':'Body'}]}
  self.assertTrue(any('requires a complete palette' in error for error in PdfService.validate_report(font_only)))
 def test_noncanonical_markdown_font_requires_complete_palette(self):
  src=self.source/'custom.md';src.write_text('# Customer\n\nBody')
  with self.assertRaises(PdfError) as error:
   PdfService(self.policy).generate_markdown(src,self.output/'custom.pdf',font='Avenir Next')
  self.assertIn('requires a complete palette',error.exception.message)
 def test_reportlab_receives_the_controller_resolved_palette(self):
  """Renderer adapters consume one resolved palette; they do not own defaults."""
  from types import SimpleNamespace
  custom={'background':'#FFFFFF','surface':'#FDFBFF','ink':'#2D123D','body':'#4C2C5B','muted':'#6B4C7A','rule':'#DEC9EA','accent':'#6D28D9','accentInk':'#FFFFFF'}
  source=self.source/'report.json'; source.write_text(json.dumps({'title':'Palette','blocks':[{'type':'paragraph','text':'Body'}],'palette':custom}))
  backend=SimpleNamespace(capabilities=SimpleNamespace(name='reportlab'))
  service=PdfService(self.policy, resolver=SimpleNamespace(resolve=lambda *_: (backend, None)))
  observed={}
  service._render=lambda document, *_: observed.setdefault('document',document)
  service.generate_report(source,self.output/'report.pdf')
  self.assertEqual(observed['document']['palette'],custom)
 def test_structured_report_crosses_the_shared_render_boundary_without_html_mutation(self):
  """ReportLab gets its dict intact; only WeasyPrint documents are HTML."""
  from types import SimpleNamespace
  from unittest.mock import patch
  captured={}
  class Backend:
   capabilities=SimpleNamespace(name='reportlab')
   def render(self,document,geometry,metadata,destination,policy):
    captured['document']=document
    destination.write_bytes(b'%PDF-sample')
  verified=SimpleNamespace(bytes=11,pages=1,version='1.4',sha256='sample',mime_type='application/pdf',warnings=())
  with patch('pdf_system.service.verify_pdf',return_value=verified):
   PdfService(self.policy)._render({'title':'Structured','palette':{}},Backend(),self.output/'report.pdf',{'Title':'Structured'},PageGeometry.from_spec('A4','portrait'))
  self.assertEqual(captured['document']['title'],'Structured')
 def test_weasyprint_object_stream_unicode_and_exact_hash(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'unicode.md';src.write_text(UNICODE_MARKDOWN,encoding='utf-8');out=self.output/'unicode.pdf'
  result=PdfService(self.policy).generate_markdown(src,out,lang='tr',deterministic=False,quality_profile='professional')
  self.assertGreater(result.pages,0);self.assertEqual(result.sha256,hashlib.sha256(out.read_bytes()).hexdigest())
  self.assertIn(b'/ObjStm',out.read_bytes());self.assertEqual(verify_pdf(out,self.policy.limits).sha256,result.sha256)
 def test_document_metadata_is_written_rather_than_dropped(self):
  """Metadata has somewhere to go other than the body of the document.

  WeasyPrint takes document metadata from the head of the HTML it is given and
  from nowhere else, so the neutral dictionary the controller builds reached
  the adapter and was discarded there: every PDF carried a producer string and
  nothing more. A title, an author or a date then had only the body left to go
  in, which is where a report's generated header block comes from.
  """
  from pdf_system.backends import WeasyPrintRenderer
  blank='<!doctype html><html><head><meta charset="utf-8"></head><body>x</body></html>'
  document=WeasyPrintRenderer()._with_metadata(blank,{'Title':'Weekly "Ops" Report','Author':'Operations','Subject':'Week 37','Lang':'tr','Creator':'chainabit-pdf'})
  self.assertIn('<html lang="tr">',document)
  self.assertIn('<title>Weekly &quot;Ops&quot; Report</title>',document)
  self.assertIn('<meta name="author" content="Operations">',document)
  self.assertIn('<meta name="description" content="Week 37">',document)
  self.assertIn('<meta name="generator" content="chainabit-pdf">',document)
  self.assertTrue(document.endswith('<body>x</body></html>'))
  # An unstated language is not a language: "und" must not become <html lang>.
  self.assertEqual(WeasyPrintRenderer()._with_metadata(blank,{'Lang':'und'}),blank)
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  from pypdf import PdfReader
  src=self.source/'m.md';src.write_text('# Report\n\nBody.',encoding='utf-8');out=self.output/'m.pdf'
  PdfService(self.policy).generate_markdown(src,out,title='Weekly Ops Report',lang='tr')
  reader=PdfReader(str(out))
  self.assertEqual(reader.metadata.get('/Title'),'Weekly Ops Report')
  self.assertEqual(str(reader.trailer['/Root'].get('/Lang')),'tr')
  self.assertNotIn('Weekly Ops Report',reader.pages[0].extract_text())
 def test_arabic_companion_font_is_embedded_offline(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'ar.md';src.write_text('# Chainabit\n\nمرحبا بالعالم — Latin Extended-A ğüşöçıİĞÜŞÖÇ',encoding='utf-8');out=self.output/'ar.pdf'
  PdfService(self.policy).generate_markdown(src,out,lang='ar',deterministic=False,quality_profile='professional')
  verification=verify_pdf(out,self.policy.limits)
  self.assertTrue(any('ArtifactArabic' in font.replace(' ','') for font in verification.fonts),verification.fonts)
 def test_resolve_direction_is_a_ratio_not_mere_presence(self):
  """Direction is a whole-document layout decision, not per-character.

  A handful of embedded Latin (a URL, a brand name) inside a majority-Arabic
  document must not flip the page to `ltr`, and a stray Arabic quote inside a
  majority-English document must not flip it to `rtl`. The Unicode
  Bidirectional Algorithm -- implemented by the renderer, never here --
  places each embedded run correctly once the page's base direction is
  right.
  """
  from pdf_system.models import is_rtl_char, resolve_direction
  self.assertEqual(resolve_direction('Quarterly Operations Report'),'ltr')
  self.assertEqual(resolve_direction('تقرير الأداء الفصلي لهذا العام'),'rtl')
  self.assertEqual(resolve_direction('مرحبا بكم في موقعنا الرسمي، انظر https://example.com للمزيد'),'rtl')
  self.assertEqual(resolve_direction('See the report; one Arabic word: مرحبا'),'ltr')
  self.assertTrue(is_rtl_char('ا'));self.assertFalse(is_rtl_char('a'))
 def test_markdown_html_sets_dir_and_logical_properties_from_content(self):
  """Direction is a LAYOUT property resolved from content, threaded into the
  HTML the WeasyPrint adapter renders -- not a second, independent field a
  caller must remember to pass, and not a naive per-character rewrite."""
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  from pdf_system.service import DEFAULT_PALETTE
  service=PdfService(self.policy)
  geometry=PageGeometry.from_spec('A4','portrait')
  rtl_document=service._markdown_html('مرحبا بكم في التقرير الفصلي لهذا العام',self.policy,'IBM Plex Sans',DEFAULT_PALETTE,True,geometry)
  self.assertIn('<html dir="rtl">',rtl_document)
  self.assertIn('direction:rtl',rtl_document)
  self.assertIn('text-align:start',rtl_document)
  self.assertIn('border-inline-start',rtl_document)
  self.assertNotIn('border-left:4pt',rtl_document)
  ltr_document=service._markdown_html('Quarterly report body, in English.',self.policy,'IBM Plex Sans',DEFAULT_PALETTE,True,geometry)
  self.assertIn('<html dir="ltr">',ltr_document)
  self.assertIn('direction:ltr',ltr_document)
 def test_report_html_direction_ignores_json_structural_keys(self):
  """Direction for a report spec comes from its STRING VALUES, walked the
  same way `validate_report` already walks them for image-as-text -- not
  from `str(spec)`, which would count the English JSON keys ('title',
  'blocks', 'paragraph', ...) as Latin filler and could understate a short
  Arabic report."""
  if not production_dependencies_available():self.skipTest('production PDF dependencies/fonts not installed')
  from pdf_system.service import DEFAULT_PALETTE
  service=PdfService(self.policy)
  geometry=PageGeometry.from_spec('A4','portrait')
  spec={'title':'تقرير الأداء الفصلي','blocks':[{'type':'paragraph','text':'مرحبا بكم في هذا التقرير'}]}
  self.assertIn('<html dir="rtl">',service._report_html(spec,self.policy,'IBM Plex Sans',DEFAULT_PALETTE,True,geometry))
 def test_with_metadata_inserts_lang_alongside_an_existing_dir_attribute(self):
  """`_html_document` now always emits `<html dir="...">`; the metadata step
  that adds `lang` afterwards must not assume a bare `<html>` it can no
  longer find. A literal-string replace on that stale assumption would
  silently stop matching, and every PDF would lose its `lang` metadata with
  no error -- exactly the kind of regression a test at the seam catches
  before a runtime one does."""
  from pdf_system.backends import WeasyPrintRenderer
  document='<!doctype html><html dir="rtl"><head><meta charset="utf-8"></head><body>x</body></html>'
  result=WeasyPrintRenderer()._with_metadata(document,{'Lang':'ar'})
  self.assertIn('dir="rtl"',result);self.assertIn('lang="ar"',result)
 def test_cjk_is_not_advertised_as_a_weasyprint_capability(self):
  """A capability report is a promise about what a caller can ask for (see
  the block comment above capability_registry in backends.py). Neither
  runtime font family this skill provisions -- IBM Plex Sans or its Arabic
  companion -- carries a single CJK glyph, and the container that installs
  this skill's font directory installs no third family either (verified
  against cloudflare-sandbox-bridge/Dockerfile, not assumed). `cjk` must
  stay off the advertised set until a real font backs it."""
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  from pdf_system.backends import capability_registry
  weasyprint=next(c for c in capability_registry() if c.name=='weasyprint')
  self.assertTrue(weasyprint.available)
  self.assertNotIn('cjk',weasyprint.supports)
  self.assertIn('rtl',weasyprint.supports)  # RTL stays fully supported: the font and the shaping engine both cover it.
 def test_cjk_document_is_refused_rather_than_rendered_with_missing_glyphs(self):
  """The exact failure this whole change turns from silent to honest: a
  Chinese/Japanese/Korean document used to render at exit 0 with every CJK
  character replaced by a missing-glyph box wherever the production image's
  fontconfig fell through to `sans-serif` and found nothing that covers CJK.
  The resolver must now refuse it by name instead."""
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'cjk.md';src.write_text('# 季度报告\n\n这是一份中文报告。',encoding='utf-8');out=self.output/'cjk.pdf'
  with self.assertRaises(PdfError) as error:
   PdfService(self.policy).generate_markdown(src,out,lang='zh',deterministic=False,quality_profile='professional')
  self.assertEqual(error.exception.code,ErrorCode.UNSUPPORTED_CAPABILITY)
  self.assertIn('cjk',(error.exception.context or {}).get('required',''))
 def test_long_document_and_page_breaks(self):
  if not production_dependencies_available():self.skipTest('production PDF dependencies not installed')
  src=self.source/'long.md';src.write_text('# Long Report\n\n'+('\n\n'.join(f'## Section {i}\n'+('ğüşöçıİĞÜŞÖÇ sample content. '*80) for i in range(40))),encoding='utf-8');out=self.output/'long.pdf'
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
  src=self.source/'unicode.md';src.write_text(UNICODE_MARKDOWN,encoding='utf-8');out=self.output/'official.pdf'
  rendered=subprocess.run([sys.executable,str(ROOT/'scripts/md_to_pdf.py'),str(src),str(out),'--lang','tr'],capture_output=True,text=True,check=False)
  self.assertEqual(rendered.returncode,0,rendered.stderr);render_message=json.loads(rendered.stdout)
  self.assertEqual(render_message['schema'],'chainabit.pdf.execution/v1');self.assertEqual(render_message['output']['sha256'],hashlib.sha256(out.read_bytes()).hexdigest());self.assertEqual(render_message['typography']['family'],'IBM Plex Sans')
  validated=subprocess.run([sys.executable,str(ROOT/'scripts/validate_pdf.py'),str(out)],capture_output=True,text=True,check=False)
  self.assertEqual(validated.returncode,0,validated.stderr);validation_message=json.loads(validated.stdout)
  self.assertEqual(validation_message['schema'],'chainabit.pdf.validation/v1');self.assertEqual(validation_message['subject']['sha256'],render_message['output']['sha256']);self.assertTrue(validation_message['fonts'])
 def test_markdown_form_feed_emits_a_page_break(self):
  """A form feed is the explicit Markdown page break.

  models.py raises the `page_breaks` requirement when it sees one, which
  constrains backend selection -- and then str.splitlines() in _markdown_html
  split on the form feed and discarded it, so the break never reached the
  HTML. A document authored as ten pages rendered as two, and validate_pdf.py
  could not detect the loss because it only bounds the page count.
  """
  try: import markdown  # noqa: F401
  except ImportError: self.skipTest('markdown is not installed')
  service=PdfService(self.policy); text="# One\nbody one\n\f# Two\nbody two\n\f# Three\nbody three"
  body='<div class="page-break"></div>'.join(service._markdown_blocks(page) for page in text.split("\f"))
  self.assertEqual(body.count('class="page-break"'),2); self.assertEqual(body.count('<h1>'),3)
  # The pre-fix path, kept as the contrast that makes the assertion mean
  # something: rendering the un-split text yields no break at all.
  self.assertEqual(service._markdown_blocks(text).count('page-break'),0)
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
