from __future__ import annotations

import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_system import PdfService, SecurityPolicy
from pdf_system.errors import ErrorCode, PdfError
from pdf_system.markdown_html import render_markdown


class RenderedMarkup(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.elements = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class MarkdownSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.policy = SecurityPolicy(root, root)

    def tearDown(self):
        self.directory.cleanup()

    def test_ordinary_identifiers_are_not_event_handler_attributes(self):
        for text in (
            '```python\ncondition_index = 1\nconnection_count = 2\n```',
            'The connection_count = 3.',
            '`onload = handler` is an assignment example.',
        ):
            with self.subTest(text=text):
                self.assertTrue(PdfService(self.policy)._preflight('markdown', text)['ok'])

    def test_code_examples_remain_inert_in_each_markdown_container(self):
        source = '<script>alert(1)</script> <span onclick="run()">sample</span>'
        for text in (
            f'```html\n{source}\n```',
            f'~~~html\n{source}\n~~~',
            f'`{source}`',
            f'    {source}',
            f'- Example:\n  ```html\n  {source}\n  ```',
            f'> ```html\n> {source}\n> ```',
        ):
            with self.subTest(text=text):
                self.assertTrue(PdfService(self.policy)._preflight('markdown', text)['ok'])
                output = RenderedMarkup(render_markdown(text, self.policy))
                self.assertTrue(any(tag == 'code' for tag, _ in output.elements))
                self.assertFalse(any(tag == 'script' or 'onclick' in attrs for tag, attrs in output.elements))

    def test_executable_markup_outside_code_is_rejected(self):
        for text in (
            '<script>alert(1)</script>', '<SCRIPT src="https://example.invalid/a"></SCRIPT>',
            '<iframe src="https://example.invalid/"></iframe>', '<object data="file:///etc/passwd"></object>',
            '<embed src="javascript:alert(1)">', '<svg><a href="javascript:alert(1)"></a></svg>',
            '<img src="x" onerror="alert(1)">', '<div onclick="alert(1)">x</div>',
            '<div ONLOAD = alert(1)>x</div>', '<div style="background:url(file:///etc/passwd)">x</div>',
            '<style>@import "https://example.invalid/a.css";</style>', '<link rel="stylesheet" href="a.css">',
            '<base href="file:///etc/">', '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
            '<a href="java&#x73;cript:alert(1)">x</a>', '<a href="java&#10;script:alert(1)">x</a>',
            '<a href="data:text/html,x">x</a>', '[x](javascript:alert%281%29)',
            '[x](data:text/html;base64,PHNjcmlwdD4=)', '[x](file:///etc/passwd)',
            'Text\n\n<script>mixed document</script>',
            '[x](java&#x73;cript:alert%281%29)',
            '<div srcdoc="&lt;script&gt;execute()&lt;/script&gt;">x</div>',
        ):
            with self.subTest(text=text):
                result = PdfService(self.policy)._preflight('markdown', text)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['code'], ErrorCode.UNSAFE_INPUT.value)
                with self.assertRaises(PdfError):
                    render_markdown(text, self.policy)

    def test_escaped_markup_and_plain_url_examples_stay_inert(self):
        for text in (
            '&lt;script&gt;alert(1)&lt;/script&gt;',
            'A JavaScript URL starts with `javascript:`.',
            r'\<script\>shown as text\</script\>',
            '[safe](https://example.invalid/path?condition_index=1)',
        ):
            with self.subTest(text=text):
                self.assertTrue(PdfService(self.policy)._preflight('markdown', text)['ok'])
                self.assertFalse(any(tag == 'script' for tag, _ in RenderedMarkup(render_markdown(text, self.policy)).elements))

    def test_code_cannot_hide_following_active_markup(self):
        for text in ('```html\n<script>example</script>\n```\n\n<script>execute</script>', '`example` <span onerror=execute()>x</span>'):
            with self.subTest(text=text):
                self.assertFalse(PdfService(self.policy)._preflight('markdown', text)['ok'])

    def test_malformed_and_encoded_display_text_never_becomes_executable(self):
        for text in ('<script src=sample', '&amp;lt;script&amp;gt;sample', '[x](java%73cript:alert%281%29)', '<!-- <script>example</script> -->'):
            with self.subTest(text=text):
                document = RenderedMarkup(render_markdown(text, self.policy))
                self.assertFalse(any(tag == 'script' for tag, _ in document.elements))
                self.assertFalse(any(attrs.get('href', '').lower().startswith('javascript:') for _, attrs in document.elements))


if __name__ == '__main__':
    unittest.main()
