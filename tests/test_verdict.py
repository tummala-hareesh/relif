import unittest

from relif.verdict import Answers, decide, CAPITAL, CURRENT, NOT_DEDUCTIBLE, PARTIAL, UNSURE


class DecideTest(unittest.TestCase):
    def check(self, outcome, **answers):
        v = decide(Answers(**answers))
        self.assertEqual(v.outcome, outcome, v)
        self.assertTrue(v.citation, v)
        self.assertTrue(v.reason, v)
        return v

    def test_repair_that_restores_is_current_8960(self):
        v = self.check(CURRENT, kind="repair", work="restores")
        self.assertEqual(v.t776_line, "8960")
        self.assertEqual(v.category, "Repairs and maintenance (8960)")
        self.assertEqual(v.deductible_pct, 100)
        self.assertFalse(v.is_capital)
        self.assertTrue(v.label.startswith("Generally deductible"))

    def test_lines_for_plain_current_kinds(self):
        for kind, line in [("advertising", "8521"), ("insurance", "8690"), ("interest", "8710"),
                           ("office", "8810"), ("professional", "8860"), ("management", "8871"),
                           ("wages", "9060"), ("property_tax", "9180"), ("travel", "9200"),
                           ("utilities", "9220"), ("vehicle", "9281")]:
            with self.subTest(kind=kind):
                self.assertEqual(self.check(CURRENT, kind=kind).t776_line, line)

    def test_vehicle_mentions_mileage_log(self):
        self.assertIn("mileage log", self.check(CURRENT, kind="vehicle").reason)

    def test_repair_that_improves_or_is_separate_asset_is_capital(self):
        self.assertEqual(self.check(CAPITAL, kind="repair", work="improves").cca_class, "1")
        v = self.check(CAPITAL, kind="repair", work="separate_asset")
        self.assertEqual(v.cca_class, "8")
        self.assertTrue(v.is_capital)
        self.assertIsNone(v.t776_line)
        self.assertIn("half-year", v.reason)

    def test_repair_without_current_capital_answer_is_unsure(self):
        self.check(UNSURE, kind="repair")
        self.check(UNSURE, kind="repair", work="unsure")

    def test_appliance_and_improvement_are_capital(self):
        self.assertEqual(self.check(CAPITAL, kind="appliance").cca_class, "8")
        self.assertEqual(self.check(CAPITAL, kind="improvement").cca_class, "1")
        self.assertEqual(self.check(CAPITAL, kind="fence").cca_class, "6")

    def test_roof_and_landscaping_are_unsure(self):
        self.check(UNSURE, kind="roof")
        self.check(UNSURE, kind="landscaping")

    def test_loan_payment_interest_vs_principal(self):
        self.assertEqual(self.check(CURRENT, kind="loan_payment", loan_part="interest").t776_line, "8710")
        self.check(NOT_DEDUCTIBLE, kind="loan_payment", loan_part="principal")
        self.check(UNSURE, kind="loan_payment")

    def test_never_deductible_kinds(self):
        for kind in ("own_labour", "penalty", "land_transfer_tax"):
            with self.subTest(kind=kind):
                v = self.check(NOT_DEDUCTIBLE, kind=kind)
                self.assertTrue(v.label.startswith("Not generally deductible"))

    def test_personal_unit_is_not_deductible(self):
        self.check(NOT_DEDUCTIBLE, kind="repair", work="restores", use="personal")

    def test_not_available_for_rent_is_unsure(self):
        self.check(UNSURE, kind="utilities", available="no")
        self.check(UNSURE, kind="utilities", available="unsure")

    def test_no_profit_expectation_is_not_deductible(self):
        self.check(NOT_DEDUCTIBLE, kind="utilities", profit="no")
        self.check(UNSURE, kind="utilities", profit="unsure")

    def test_short_term_rental_compliance(self):
        self.check(CURRENT, kind="utilities", short_term=True, str_compliant="yes")
        self.check(NOT_DEDUCTIBLE, kind="utilities", short_term=True, str_compliant="no")
        self.check(UNSURE, kind="utilities", short_term=True)

    def test_shared_use_applies_rental_pct(self):
        v = self.check(PARTIAL, kind="property_tax", use="shared", rental_pct=25)
        self.assertEqual(v.deductible_pct, 25)
        self.assertEqual(v.t776_line, "9180")
        self.assertIn("25%", " ".join(v.assumptions))

    def test_personal_portion_reduces_pct(self):
        v = self.check(PARTIAL, kind="repair", work="restores", use="shared", rental_pct=50, personal_pct=20)
        self.assertEqual(v.deductible_pct, 40)
        self.assertIn("9949", v.reason)

    def test_rented_unit_ignores_property_pct(self):
        self.assertEqual(self.check(CURRENT, kind="repair", work="restores", use="rental",
                                    rental_pct=25).deductible_pct, 100)

    def test_partial_capital_stays_capital(self):
        v = self.check(CAPITAL, kind="appliance", use="shared", rental_pct=50)
        self.assertEqual(v.deductible_pct, 50)

    def test_unknown_or_missing_kind_is_unsure(self):
        self.check(UNSURE, kind="other")
        self.check(UNSURE)

    def test_bad_answers_raise(self):
        for bad in (dict(kind="yacht"), dict(use="sometimes"), dict(rental_pct=101), dict(personal_pct=-1),
                    dict(work="maybe"), dict(loan_part="both"), dict(available="perhaps")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                decide(Answers(**bad))

    def test_label_never_says_you_can_claim(self):
        for kind in ("repair", "appliance", "penalty", "other", "utilities"):
            v = decide(Answers(kind=kind, work="restores"))
            self.assertNotIn("you can claim", v.label.lower())
            self.assertIn("Not tax advice", v.disclaimer)


if __name__ == "__main__":
    unittest.main()
