import unittest

from relif.money import format_cents, parse_cents


class MoneyTest(unittest.TestCase):
    def test_parse(self):
        for text, cents in [("42.10", 4210), ("$1,234.5", 123450), ("7", 700), ("-3.05", -305), ("0.10", 10)]:
            with self.subTest(text):
                self.assertEqual(parse_cents(text), cents)
        for bad in ["abc", "1.234", ""]:
            with self.subTest(bad), self.assertRaises(ValueError):
                parse_cents(bad)

    def test_format(self):
        self.assertEqual(format_cents(4210), "42.10")
        self.assertEqual(format_cents(-305), "-3.05")
        self.assertEqual(format_cents(5), "0.05")
