"""Visual obligations are structural; ordinary technical prose stays valid."""
import unittest
from test_media_layouts import DECK_MODULE


class SpecCompletenessTests(unittest.TestCase):
    def errors(self, slide):
        return DECK_MODULE.validate_spec({'title': 'Engineering', 'slides': [slide]})

    def test_technical_marker_mentions_are_legitimate_content(self):
        for text in ('Evaluate TODO markers in source code', 'Track FIXME annotations',
                     'The parser recognizes TBD tokens', 'Explain lorem ipsum test fixtures'):
            with self.subTest(text=text):
                self.assertEqual(self.errors({'layout': 'content', 'title': text,
                                             'bullets': ['Explain the parser behavior']}), [])

    def test_explicit_visual_requirements_cannot_be_satisfied_by_prose(self):
        errors = self.errors({'layout': 'content', 'title': 'Revenue',
                             'bullets': ['Growth is 18%'], 'requiredVisuals': ['chart']})
        self.assertTrue(any('requiredVisuals' in error for error in errors), errors)

    def test_visual_requirements_are_a_closed_bounded_contract(self):
        for value in ('chart', ['unknown'], ['image', 'image'], ['image'] * 20):
            with self.subTest(value=value):
                errors = self.errors({'layout': 'title', 'title': 'Invalid', 'requiredVisuals': value})
                self.assertTrue(any('requiredVisuals' in error for error in errors), errors)

    def test_native_chart_satisfies_its_visual_requirement(self):
        self.assertEqual(self.errors({
            'layout': 'chart', 'title': 'Revenue', 'requiredVisuals': ['chart'],
            'chart': {'kind': 'bar', 'categories': ['A', 'B'],
                      'series': [{'name': 'Revenue', 'values': [18, 24]}]},
        }), [])
