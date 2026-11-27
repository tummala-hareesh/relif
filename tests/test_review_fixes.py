"""Edge cases and tamper scenarios found in review of PR #2."""
import json
import os
import shutil
import sqlite3
import subprocess

from relif.backup import backup, restore_test
from relif.export import check_manifest, export_year
from relif.money import parse_cents
from relif.store import RelifError, Store, event_hash
from tests.helpers import StoreTestCase
from tests.test_backup import ENV, FakeRestic

WHICH = lambda _: "/usr/bin/restic"  # noqa: E731


class GuardTest(StoreTestCase):
    def test_input_guards_leave_no_events(self):
        pid = self.store.add_property("Unit A", acquired_on="2019-01-01")
        before = len(self.store.events())
        cases = [
            ("empty property name", lambda: self.store.add_property("")),
            ("rented pct", lambda: self.store.add_property("B", rented_pct=101)),
            ("unknown property", lambda: self.store.add(amount_cents=1, date="2026-01-01", vendor="A", property_id=9)),
            ("empty vendor", lambda: self.store.add(amount_cents=1, date="2026-01-01", vendor="")),
            ("bool amount", lambda: self.store.add(amount_cents=True, date="2026-01-01", vendor="A")),
            ("huge amount", lambda: self.store.add(amount_cents=2 ** 70, date="2026-01-01", vendor="A")),
            ("tax year too big", lambda: self.store.add(amount_cents=1, date="2026-01-01", vendor="A", tax_year=9995)),
            ("tax year zero", lambda: self.store.add(amount_cents=1, date="2026-01-01", vendor="A", tax_year=0)),
            ("dispose missing", lambda: self.store.dispose_property(9, "2030-01-01")),
            ("dispose before acquired", lambda: self.store.dispose_property(pid, "2018-01-01")),
            ("attach to missing entry", lambda: self.store.attach(99, self.receipt())),
            ("void missing", lambda: self.store.void(99, "x")),
        ]
        for name, call in cases:
            with self.subTest(name), self.assertRaises(RelifError):
                call()
        eid = self.store.add(amount_cents=1, date="2026-01-01", vendor="A")
        with self.assertRaises(RelifError):
            self.store.void(eid, "")
        self.assertEqual(len(self.store.events()), before + 1)

    def test_money_rejects_non_finite_and_huge(self):
        for bad in ["Infinity", "NaN", "1e100000", "1e20"]:
            with self.subTest(bad), self.assertRaises(ValueError):
                parse_cents(bad)

    def test_schema_version_guard(self):
        self.store.conn.execute("PRAGMA user_version = 99")
        with self.assertRaises(RelifError) as ctx:
            Store(self.home)
        self.assertIn("schema version", str(ctx.exception))

    def test_store_level_retention_inputs(self):
        a = self.store.add(amount_cents=1, date="2026-01-05", vendor="A", tax_year=2025)
        b = self.store.add(amount_cents=1, date="2026-01-05", vendor="B", filed_late_on="2028-03-15")
        c = self.store.add(amount_cents=1, date="2026-01-05", vendor="C", on_hold=True)
        self.assertEqual(self.store.entry(a)["retain_until"], "2031-12-31")
        self.assertEqual(self.store.entry(b)["retain_until"], "2034-03-15")
        self.assertIsNone(self.store.entry(c)["retain_until"])

    def test_search_escapes_like_wildcards(self):
        self.store.add(amount_cents=1, date="2026-01-01", vendor="100% Paint")
        self.store.add(amount_cents=1, date="2026-01-01", vendor="1000 Paint")
        self.assertEqual([e["vendor"] for e in self.store.entries(search="100%")], ["100% Paint"])

    def test_attach_and_non_ascii_vendor(self):
        eid = self.store.add(amount_cents=1, date="2026-01-01", vendor="Quincaillerie Émile")
        self.store.attach(eid, self.receipt())
        self.assertEqual(len(self.store.receipts_for(eid)), 1)
        self.store.close()
        self.store = Store(self.home)
        self.assertTrue(self.store.verify().ok)


class VerifyHardeningTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.e1 = self.store.add(amount_cents=4210, date="2026-03-04", vendor="Hardware Co",
                                 receipts=[self.receipt()])

    def test_malformed_anchor_lines_are_problems_not_crashes(self):
        with open(self.home / "anchors.log", "a") as f:
            f.write('not json\n[1]\n{}\n{"seq":"5","ts":"x","head":"y"}\n')
        problems = self.store.verify().problems
        self.assertEqual(sum("not a valid anchor" in p for p in problems), 4, problems)

    def test_truncation_after_export_caught_via_bundle_anchor(self):
        export_year(self.store, 2026)
        self.store.add(amount_cents=1, date="2026-06-01", vendor="Later")
        (self.home / "anchors.log").write_text("")  # attacker empties the live anchor log
        problems = self.store.verify().problems
        self.assertTrue(any("missing from anchors.log" in p for p in problems), problems)

    def test_unappliable_event_is_reported(self):
        self.store.conn.execute("DROP TRIGGER events_no_update")
        seq, prev = self.store.head()
        row = self.store.conn.execute("SELECT * FROM events WHERE seq = ?", (seq,)).fetchone()
        payload = json.loads(row["payload_json"])
        payload["sha256"] = "../../etc/passwd"
        new_hash = event_hash(row["prev_hash"], seq, row["ts"], row["kind"], row["entry_id"], payload)
        with self.store.conn:
            self.store.conn.execute("UPDATE events SET payload_json = ?, row_hash = ? WHERE seq = ?",
                                    (json.dumps(payload), new_hash, seq))
        problems = self.store.verify().problems
        self.assertTrue(any("cannot be applied" in p for p in problems), problems)

    def test_rebuild_repairs_drift(self):
        with self.store.conn:
            self.store.conn.execute("UPDATE entries SET amount_cents = 1")
        self.assertFalse(self.store.verify().ok)
        self.store.rebuild()
        self.assertEqual(self.store.entry(self.e1)["amount_cents"], 4210)
        self.assertTrue(self.store.verify().ok)


class ExportHardeningTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.eid = self.store.add(amount_cents=4210, date="2026-03-04", vendor="=HYPERLINK(\"x\")",
                                  receipts=[self.receipt(), self.receipt("b.pdf", b"second"),
                                            self.receipt("noext", b"third")])

    def test_refuses_to_export_a_tampered_ledger(self):
        (r,) = [r for r in self.store.receipts_for(self.eid) if r["ext"] == ""]
        path = self.store.receipt_path(r["sha256"], r["ext"])
        os.chmod(path, 0o644)
        path.write_bytes(b"forged")
        with self.assertRaises(RelifError):
            export_year(self.store, 2026)
        self.assertFalse((self.home / "exports" / "2026").exists())
        self.assertEqual(self.store.anchors(), [])
        # even when forced past verify, a forged receipt is never copied into a bundle
        with self.assertRaises(RelifError):
            export_year(self.store, 2026, allow_unverified=True)
        self.assertEqual(self.store.anchors(), [])

    def test_names_collisions_and_formula_cells(self):
        out = export_year(self.store, 2026)
        names = sorted(p.name for p in (out / "receipts").iterdir())
        base = f"2026-03-04_hyperlink-x_42.10_{self.eid}"
        self.assertEqual(names, sorted([base, f"{base}.pdf", f"{base}-2.pdf"]))
        self.assertIn("'=HYPERLINK", (out / "ledger.csv").read_text())

    def test_check_manifest_malformed_and_escaping_lines(self):
        out = export_year(self.store, 2026)
        with open(out / "MANIFEST.sha256", "a") as f:
            f.write("garbage\n" + "0" * 64 + "  ../outside.txt\n")
        problems = check_manifest(out)
        self.assertTrue(any("malformed" in p for p in problems), problems)
        self.assertTrue(any("outside the bundle" in p for p in problems), problems)


class BackupFailureTest(StoreTestCase):
    def test_restic_failures(self):
        def failing(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "repository locked")
        res = backup(self.home, runner=failing, which=WHICH, env=ENV)
        self.assertFalse(res.ok)
        self.assertIn("restic backup failed: repository locked", res.message)
        res = restore_test(self.home, runner=failing, which=WHICH, env=ENV)
        self.assertIn("restic restore failed", res.message)

    def test_restore_without_store_does_not_open(self):
        def empty_restore(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        res = restore_test(self.home, runner=empty_restore, which=WHICH, env=ENV)
        self.assertFalse(res.ok)
        self.assertIn("does not open", res.message)

    def test_missing_password_skips_and_runner_gets_env(self):
        res = backup(self.home, which=WHICH, env={"RESTIC_REPOSITORY": "/r"})
        self.assertTrue(res.skipped)
        self.assertIn("password", res.message)
        seen = {}

        def spy(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        backup(self.home, runner=spy, which=WHICH, env=ENV)
        self.assertEqual(seen["env"]["RESTIC_REPOSITORY"], "/tmp/repo")
        self.assertIs(seen["stdin"], subprocess.DEVNULL)
        self.assertIn("timeout", seen)
