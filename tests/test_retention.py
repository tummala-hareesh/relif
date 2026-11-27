import unittest
from datetime import date

from relif.retention import retention


class RetentionTableTest(unittest.TestCase):
    def test_table(self):
        cases = [
            # name, kwargs, expected retain_until, expected hold, rule keyword
            ("normal year", dict(tax_year=2026), date(2032, 12, 31), False, "end of the tax year"),
            ("late filing", dict(tax_year=2026, filed_late_on=date(2028, 3, 15)),
             date(2034, 3, 15), False, "filed late"),
            ("late filing earlier than default is ignored",
             dict(tax_year=2026, filed_late_on=date(2026, 6, 1)), date(2032, 12, 31), False, "end of the tax year"),
            ("objection hold", dict(tax_year=2026, on_hold=True), None, True, "objection"),
            ("capital held", dict(tax_year=2026, is_capital=True), None, True, "capital"),
            ("capital disposed", dict(tax_year=2020, is_capital=True, disposed_on=date(2030, 5, 1)),
             date(2036, 12, 31), False, "disposed"),
            ("hold beats capital disposed",
             dict(tax_year=2020, is_capital=True, disposed_on=date(2030, 5, 1), on_hold=True), None, True, "objection"),
        ]
        for name, kwargs, until, hold, keyword in cases:
            with self.subTest(name):
                r = retention(**kwargs)
                self.assertEqual(r.retain_until, until)
                self.assertEqual(r.hold, hold)
                self.assertIn(keyword, r.rule)
                self.assertIn("CRA", r.citation)

    def test_eligible(self):
        r = retention(tax_year=2020)
        self.assertTrue(r.eligible_for_deletion(date(2027, 1, 1)))
        self.assertFalse(r.eligible_for_deletion(date(2026, 12, 31)))
        self.assertFalse(retention(tax_year=2020, is_capital=True).eligible_for_deletion(date(2099, 1, 1)))

    def test_leap_day_late_filing(self):
        r = retention(tax_year=2026, filed_late_on=date(2028, 2, 29))
        self.assertEqual(r.retain_until, date(2034, 2, 28))


if __name__ == "__main__":
    unittest.main()
