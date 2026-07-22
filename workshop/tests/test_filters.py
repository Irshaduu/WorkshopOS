from django.test import TestCase
from datetime import date, timedelta
from workshop.templatetags.custom_filters import is_tomorrow, divide, multiply, clean_qty, get_range

class CustomFiltersTestCase(TestCase):
    def test_is_tomorrow(self):
        tomorrow = date.today() + timedelta(days=1)
        self.assertTrue(is_tomorrow(tomorrow))
        self.assertFalse(is_tomorrow(date.today()))
        self.assertFalse(is_tomorrow(None))

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5.0)
        self.assertEqual(divide(10, 0), 0)
        self.assertEqual(divide(10, None), 0)
        self.assertEqual(divide(10, "abc"), 0)
        self.assertEqual(divide("abc", 2), 0)

    def test_multiply(self):
        self.assertEqual(multiply(10, 2), 20.0)
        self.assertEqual(multiply(10, "abc"), 0)
        self.assertEqual(multiply("abc", 2), 0)
        self.assertEqual(multiply(10, None), 0)

    def test_clean_qty(self):
        self.assertEqual(clean_qty(None), "")
        self.assertEqual(clean_qty(1.0), 1)
        self.assertEqual(clean_qty(1.5), 1.5)
        self.assertEqual(clean_qty("abc"), "abc")

    def test_get_range(self):
        self.assertEqual(list(get_range(5)), [0, 1, 2, 3, 4])
        self.assertEqual(list(get_range("abc")), [])
        self.assertEqual(list(get_range(None)), [])
