import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from relif.cli import main


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = str(self.tmp / "home")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = main(["--home", self.home, *argv], out=out)
        return code, out.getvalue() + err.getvalue()

    def test_full_flow(self):
        receipt = self.tmp / "r.pdf"
        receipt.write_bytes(b"receipt")
        self.assertEqual(self.run_cli("init")[0], 0)
        self.assertEqual(self.run_cli("property", "add", "Unit A", "--rented-pct", "60")[0], 0)
        code, out = self.run_cli("add", "42.10", "Hardware Co", "--date", "2026-03-04", "--property", "1",
                                 "--category", "Repairs (8960)", "-r", str(receipt))
        self.assertEqual(code, 0, out)
        self.assertIn("keep until 2032-12-31", out)
        self.run_cli("add", "5", "Typo", "--date", "2026-03-05")
        self.assertEqual(self.run_cli("void", "2", "--reason", "typo")[0], 0)
        code, out = self.run_cli("list", "--year", "2026")
        self.assertIn("42.10", out)
        self.assertIn("VOID", out)
        code, out = self.run_cli("export", "--year", "2026")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("2026: 2 entries, 1 receipts", out)
        self.assertIn("chain intact", out)
        code, out = self.run_cli("retention", "--today", "2033-01-01")
        self.assertIn("2 entries past retention", out)

    def test_errors_exit_2(self):
        self.assertEqual(self.run_cli("list")[0], 2)
        self.run_cli("init")
        code, out = self.run_cli("add", "4.2.1", "X")
        self.assertEqual(code, 2)
        self.assertIn("not an amount", out)

    def test_verify_failure_exit_1(self):
        self.run_cli("init")
        receipt = self.tmp / "r.pdf"
        receipt.write_bytes(b"receipt")
        self.run_cli("add", "1", "A", "-r", str(receipt))
        for f in (Path(self.home) / "receipts").rglob("*.pdf"):
            f.unlink()
        code, out = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("receipt missing", out)
