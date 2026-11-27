import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from relif.capture import answers_for, log_expense
from relif.cli import main
from relif.store import RelifError
from relif.verdict import Answers, decide
from tests.helpers import StoreTestCase


class CaptureTest(StoreTestCase):
    def test_shared_use_takes_rented_pct_from_property(self):
        pid = self.store.add_property("Duplex", rented_pct=25)
        a = answers_for(self.store, pid, kind="property_tax", use="shared")
        self.assertEqual(a.rental_pct, 25)
        a = answers_for(self.store, pid, kind="property_tax", use="shared", rental_pct=40)
        self.assertEqual(a.rental_pct, 40)

    def test_unknown_property_raises(self):
        with self.assertRaises(RelifError):
            answers_for(self.store, 99, kind="repair")

    def test_log_expense_records_verdict_fields(self):
        v = decide(Answers(kind="appliance"))
        eid = log_expense(self.store, v, amount_cents=90000, date="2026-03-04", vendor="Appliance Co",
                          receipts=[self.receipt()])
        e = self.store.entry(eid)
        self.assertEqual(e["verdict"], v.label)
        self.assertEqual(e["cca_class"], "8")
        self.assertEqual(e["is_capital"], 1)
        self.assertEqual(len(self.store.receipts_for(eid)), 1)
        self.assertTrue(self.store.verify().ok)

    def test_log_current_sets_category(self):
        eid = log_expense(self.store, decide(Answers(kind="repair", work="restores")), amount_cents=4210,
                          date="2026-03-04", vendor="Hardware Co")
        self.assertEqual(self.store.entry(eid)["category"], "Repairs and maintenance (8960)")


class AskCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-ask-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = str(self.tmp / "home")
        self.run_cli("init")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = main(["--home", self.home, *argv], out=out)
        return code, out.getvalue() + err.getvalue()

    def test_ask_prints_verdict_without_logging(self):
        code, out = self.run_cli("ask", "--kind", "repair", "--work", "restores")
        self.assertEqual(code, 0, out)
        self.assertIn("Generally deductible: Repairs and maintenance (8960)", out)
        self.assertIn("T4036", out)
        self.assertIn("Not tax advice", out)
        self.assertIn("0 entries", self.run_cli("list")[1])

    def test_ask_and_log(self):
        self.run_cli("property", "add", "Duplex", "--rented-pct", "50")
        receipt = self.tmp / "r.jpg"
        receipt.write_bytes(b"photo")
        code, out = self.run_cli("ask", "--kind", "utilities", "--use", "shared", "--property", "1",
                                 "--amount", "120.00", "--vendor", "Hydro", "--date", "2026-02-01",
                                 "-r", str(receipt))
        self.assertEqual(code, 0, out)
        self.assertIn("Partly deductible: 50%", out)
        self.assertIn("entry #1", out)
        code, out = self.run_cli("list")
        self.assertIn("Utilities (9220)", out)
        self.assertIn("[1 receipt]", out)

    def test_amount_without_vendor_is_an_error(self):
        code, out = self.run_cli("ask", "--kind", "repair", "--amount", "5")
        self.assertEqual(code, 2)
        self.assertIn("--vendor", out)

    def test_bad_pct_is_an_error(self):
        code, out = self.run_cli("ask", "--kind", "repair", "--personal-pct", "150")
        self.assertEqual(code, 2)
        self.assertIn("personal_pct", out)


if __name__ == "__main__":
    unittest.main()
