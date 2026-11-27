import os

from tests.helpers import StoreTestCase


class VerifyTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.e1 = self.store.add(amount_cents=4210, date="2026-03-04", vendor="Hardware Co",
                                 receipts=[self.receipt()])
        self.e2 = self.store.add(amount_cents=9900, date="2026-04-01", vendor="Plumber")
        self.e3 = self.store.add(amount_cents=1500, date="2026-05-01", vendor="Paint")

    def drop_triggers(self):
        self.store.conn.execute("DROP TRIGGER events_no_update")
        self.store.conn.execute("DROP TRIGGER events_no_delete")

    def test_clean_store_verifies(self):
        report = self.store.verify()
        self.assertTrue(report.ok, report.problems)
        self.assertEqual(report.events, 4)
        self.assertEqual(report.receipts, 1)
        self.assertEqual(report.years[2026], (3, 1))

    def test_edited_row_is_caught(self):
        self.drop_triggers()
        with self.store.conn:
            self.store.conn.execute(
                "UPDATE events SET payload_json = replace(payload_json, '9900', '99000') WHERE seq = 3")
        self.assertTrue(any("modified" in p for p in self.store.verify().problems))

    def test_deleted_row_is_caught(self):
        self.drop_triggers()
        with self.store.conn:
            self.store.conn.execute("DELETE FROM events WHERE seq = 2")
        problems = self.store.verify().problems
        self.assertTrue(any("gap" in p for p in problems), problems)

    def test_truncated_tail_is_caught_by_anchor(self):
        self.store.anchor("test")
        self.drop_triggers()
        with self.store.conn:
            self.store.conn.execute("DELETE FROM events WHERE seq = 4")
            self.store.conn.execute("DELETE FROM entries WHERE id = 3")
        problems = self.store.verify().problems
        self.assertTrue(any("truncated" in p for p in problems), problems)

    def test_rewritten_history_does_not_match_anchor(self):
        self.store.anchor("test")
        self.drop_triggers()
        with self.store.conn:
            self.store.conn.execute("UPDATE events SET row_hash = 'f' || substr(row_hash, 2) WHERE seq = 4")
        problems = self.store.verify().problems
        self.assertTrue(any("anchored" in p for p in problems), problems)

    def test_modified_receipt_is_caught(self):
        (r,) = self.store.receipts_for(self.e1)
        path = self.store.receipt_path(r["sha256"], r["ext"])
        os.chmod(path, 0o644)
        path.write_bytes(path.read_bytes() + b"x")
        self.assertTrue(any("receipt modified" in p for p in self.store.verify().problems))

    def test_missing_receipt_is_caught(self):
        (r,) = self.store.receipts_for(self.e1)
        self.store.receipt_path(r["sha256"], r["ext"]).unlink()
        self.assertTrue(any("receipt missing" in p for p in self.store.verify().problems))

    def test_edited_derived_table_is_caught(self):
        with self.store.conn:
            self.store.conn.execute("UPDATE entries SET amount_cents = 1 WHERE id = ?", (self.e2,))
        self.assertTrue(any("derived" in p for p in self.store.verify().problems))
