import os
import sqlite3

from relif.store import GENESIS, RelifError, Store, event_hash
from tests.helpers import StoreTestCase


class InitTest(StoreTestCase):
    def test_layout_and_schema(self):
        self.assertTrue((self.home / "relif.db").exists())
        self.assertTrue((self.home / "receipts").is_dir())
        self.assertEqual(self.store.conn.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(self.store.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.store.head(), (0, GENESIS))

    def test_init_is_idempotent(self):
        self.store.add(amount_cents=100, date="2026-01-02", vendor="A")
        again = Store.init(self.home)
        self.assertEqual(len(again.entries()), 1)
        again.close()

    def test_open_missing_store(self):
        with self.assertRaises(RelifError):
            Store(self.tmp / "nope")


class EventLogTest(StoreTestCase):
    def test_events_are_append_only(self):
        self.store.add(amount_cents=100, date="2026-01-02", vendor="A")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute("UPDATE events SET payload_json = '{}'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.conn.execute("DELETE FROM events")

    def test_hash_chain_links_rows(self):
        self.store.add(amount_cents=100, date="2026-01-02", vendor="A")
        self.store.add(amount_cents=200, date="2026-01-03", vendor="B")
        e1, e2 = self.store.events()
        self.assertEqual(e1["prev_hash"], GENESIS)
        self.assertEqual(e2["prev_hash"], e1["row_hash"])
        import json
        self.assertEqual(e2["row_hash"], event_hash(e2["prev_hash"], 2, e2["ts"], e2["kind"], e2["entry_id"],
                                                    json.loads(e2["payload_json"])))


class EntriesTest(StoreTestCase):
    def test_add_and_list(self):
        pid = self.store.add_property("Unit A", address="1 Main St", rented_pct=60, acquired_on="2019-05-01")
        eid = self.store.add(amount_cents=4210, date="2026-03-04", vendor="Hardware Co",
                             description="caulk", property_id=pid, category="Repairs (8960)",
                             receipts=[self.receipt()])
        e = self.store.entry(eid)
        self.assertEqual(e["amount_cents"], 4210)
        self.assertEqual(e["tax_year"], 2026)
        self.assertEqual(e["retain_until"], "2032-12-31")
        self.assertIn("6 years", e["retention_rule"])
        self.assertEqual(len(self.store.receipts_for(eid)), 1)
        self.assertEqual([r["id"] for r in self.store.entries(year=2026, search="caulk")], [eid])
        self.assertEqual(self.store.entries(year=2025), [])

    def test_amount_must_be_integer_cents(self):
        with self.assertRaises(RelifError):
            self.store.add(amount_cents=42.1, date="2026-03-04", vendor="X")

    def test_bad_date_and_missing_receipt(self):
        with self.assertRaises(RelifError):
            self.store.add(amount_cents=1, date="03/04/2026", vendor="X")
        with self.assertRaises(RelifError):
            self.store.add(amount_cents=1, date="2026-03-04", vendor="X", receipts=[self.tmp / "missing.pdf"])
        self.assertEqual(self.store.events(), [])

    def test_void_keeps_history(self):
        eid = self.store.add(amount_cents=100, date="2026-01-02", vendor="A")
        self.store.void(eid, "typo, re-entered as #2")
        self.assertEqual(self.store.entry(eid)["voided"], 1)
        self.assertEqual([e["kind"] for e in self.store.events()], ["entry.created", "entry.voided"])
        self.assertEqual(self.store.entries(include_voided=False), [])
        with self.assertRaises(RelifError):
            self.store.void(eid, "again")

    def test_capital_retention_follows_property_disposal(self):
        pid = self.store.add_property("Unit A")
        eid = self.store.add(amount_cents=500000, date="2020-06-01", vendor="Roofer", property_id=pid,
                             is_capital=True, cca_class="1")
        self.assertIsNone(self.store.entry(eid)["retain_until"])
        self.store.dispose_property(pid, "2030-05-01")
        self.assertEqual(self.store.entry(eid)["retain_until"], "2036-12-31")


class ReceiptStoreTest(StoreTestCase):
    def test_content_addressed_read_only_and_deduplicated(self):
        r = self.receipt()
        a = self.store.add(amount_cents=1, date="2026-01-01", vendor="A", receipts=[r])
        b = self.store.add(amount_cents=1, date="2026-01-02", vendor="A", receipts=[self.receipt("copy.PDF")])
        (ra,), (rb,) = self.store.receipts_for(a), self.store.receipts_for(b)
        self.assertEqual(ra["sha256"], rb["sha256"])
        path = self.store.receipt_path(ra["sha256"], ra["ext"])
        self.assertEqual(path.parent.parent.name, ra["sha256"][:2])
        self.assertEqual(path.read_bytes(), r.read_bytes())
        self.assertFalse(os.stat(path).st_mode & 0o222)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)


class RebuildTest(StoreTestCase):
    def test_rebuild_matches_live_state(self):
        pid = self.store.add_property("Unit A")
        eid = self.store.add(amount_cents=100, date="2026-01-02", vendor="A", property_id=pid,
                             receipts=[self.receipt()])
        self.store.void(eid, "dup")
        before = [tuple(r) for r in self.store.entries()]
        self.store.rebuild()
        self.assertEqual([tuple(r) for r in self.store.entries()], before)
        self.assertEqual(self.store._derived_drift(), [])
