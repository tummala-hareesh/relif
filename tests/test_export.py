import csv

from relif.export import check_manifest, export_year
from relif.store import RelifError
from tests.helpers import StoreTestCase


class ExportTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        pid = self.store.add_property("Unit A")
        self.a = self.store.add(amount_cents=4210, date="2026-03-04", vendor="Hardware Co", property_id=pid,
                                category="Repairs (8960)", receipts=[self.receipt()])
        self.b = self.store.add(amount_cents=9900, date="2026-04-01", vendor="Hardware Co", property_id=pid,
                                category="Repairs (8960)", receipts=[self.receipt("b.jpg", b"jpeg")])
        self.v = self.store.add(amount_cents=100, date="2026-04-02", vendor="Oops", property_id=pid,
                                category="Repairs (8960)")
        self.store.void(self.v, "duplicate")
        self.c = self.store.add(amount_cents=500000, date="2026-06-01", vendor="Roofer", property_id=pid,
                                is_capital=True, cca_class="1")
        self.store.add(amount_cents=1, date="2025-12-31", vendor="Last year")

    def read(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_bundle_contents(self):
        out = export_year(self.store, 2026)
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ["MANIFEST.sha256", "README.txt", "anchors.log", "capital_register.csv",
                                 "ledger.csv", "receipts", "t776_summary.csv"])
        ledger = self.read(out / "ledger.csv")
        self.assertEqual([r["entry_id"] for r in ledger], [str(i) for i in (self.a, self.b, self.v, self.c)])
        self.assertEqual(ledger[2]["voided"], "yes")
        self.assertEqual(ledger[0]["receipts"], f"2026-03-04_hardware-co_42.10_{self.a}.pdf")
        self.assertTrue((out / "receipts" / ledger[0]["receipts"]).exists())
        self.assertEqual(self.read(out / "t776_summary.csv"),
                         [{"property": "Unit A", "category": "Repairs (8960)", "total": "141.10"}])
        self.assertEqual([r["vendor"] for r in self.read(out / "capital_register.csv")], ["Roofer"])
        readme = (out / "README.txt").read_text()
        self.assertIn("not tax advice", readme)
        self.assertIn(self.store.head()[1], readme)
        self.assertEqual(check_manifest(out), [])

    def test_export_anchors_the_chain(self):
        export_year(self.store, 2026)
        (anchor,) = self.store.anchors()
        self.assertEqual((anchor["seq"], anchor["head"]), self.store.head())
        self.assertTrue(self.store.verify().ok)

    def test_manifest_catches_changes(self):
        out = export_year(self.store, 2026)
        with open(out / "ledger.csv", "a") as f:
            f.write("x\n")
        (out / "extra.txt").write_text("x")
        problems = check_manifest(out)
        self.assertIn("changed: ledger.csv", problems)
        self.assertIn("not in manifest: extra.txt", problems)

    def test_existing_bundle_needs_force(self):
        export_year(self.store, 2026)
        with self.assertRaises(RelifError):
            export_year(self.store, 2026)
        self.store.add(amount_cents=5, date="2026-07-01", vendor="Late addition")
        out = export_year(self.store, 2026, force=True)
        self.assertEqual(len(self.read(out / "ledger.csv")), 5)
        self.assertEqual(len(self.store.anchors()), 2)
