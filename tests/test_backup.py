import shutil
import subprocess
from pathlib import Path

from relif.backup import backup, restore_test
from tests.helpers import StoreTestCase

ENV = {"RESTIC_REPOSITORY": "/tmp/repo", "RESTIC_PASSWORD": "x"}


class FakeRestic:
    """Stands in for restic: `backup` snapshots the folder, `restore` copies it back under --target."""

    def __init__(self, tamper=None):
        self.snapshot = None
        self.calls = []
        self.tamper = tamper

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[1] == "backup":
            src = Path(cmd[2])
            self.snapshot = (src, Path(kw.get("_tmp", src.parent / "snapshot")))
            shutil.copytree(src, self.snapshot[1])
        elif cmd[1] == "restore":
            src, snap = self.snapshot
            dest = Path(cmd[cmd.index("--target") + 1]) / str(src).lstrip("/")
            shutil.copytree(snap, dest)
            if self.tamper:
                self.tamper(dest)
        return subprocess.CompletedProcess(cmd, 0, "", "")


class BackupTest(StoreTestCase):
    def test_skips_cleanly_without_restic(self):
        res = backup(self.home, which=lambda _: None, env=ENV)
        self.assertTrue(res.ok and res.skipped)
        self.assertIn("not installed", res.message)
        res = restore_test(self.home, which=lambda _: "/usr/bin/restic", env={})
        self.assertTrue(res.ok and res.skipped)

    def test_round_trip(self):
        self.store.add(amount_cents=100, date="2026-01-02", vendor="A", receipts=[self.receipt()])
        self.store.anchor("before backup")
        fake = FakeRestic()
        which = lambda _: "/usr/bin/restic"  # noqa: E731
        res = backup(self.home, runner=fake, which=which, env=ENV)
        self.assertTrue(res.ok and not res.skipped, res.message)
        self.assertIn("--keep-yearly 10", res.message)
        res = restore_test(self.home, runner=fake, which=which, env=ENV)
        self.assertTrue(res.ok, res.message)
        self.assertIn("chain intact", res.message)

    def test_restore_of_damaged_backup_fails(self):
        self.store.add(amount_cents=100, date="2026-01-02", vendor="A", receipts=[self.receipt()])
        fake = FakeRestic(tamper=lambda d: [p.unlink() for p in (d / "receipts").rglob("*.pdf")])
        which = lambda _: "/usr/bin/restic"  # noqa: E731
        backup(self.home, runner=fake, which=which, env=ENV)
        res = restore_test(self.home, runner=fake, which=which, env=ENV)
        self.assertFalse(res.ok)
        self.assertIn("receipt missing", res.message)
