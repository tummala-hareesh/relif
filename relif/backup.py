"""Backups are delegated to restic; RELIF only runs it and proves a restore works.

restic is optional: without the binary or a configured repository (RESTIC_REPOSITORY or
RESTIC_REPOSITORY_FILE, plus a password source) these commands skip cleanly.
Suggested retention policy (run it yourself, after a --dry-run):
    restic forget --keep-monthly 24 --keep-yearly 10 --dry-run
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .store import Store

POLICY = "restic forget --keep-monthly 24 --keep-yearly 10 --dry-run"


@dataclass
class Result:
    ok: bool
    skipped: bool
    message: str


def _unavailable(env, which):
    if which("restic") is None:
        return "restic is not installed; backup skipped (install restic to enable it)"
    if not (env.get("RESTIC_REPOSITORY") or env.get("RESTIC_REPOSITORY_FILE")):
        return "no restic repository configured (set RESTIC_REPOSITORY); backup skipped"
    return None


def backup(root, *, runner=subprocess.run, which=shutil.which, env=None) -> Result:
    env = os.environ if env is None else env
    why = _unavailable(env, which)
    if why:
        return Result(True, True, why)
    root = Path(root).expanduser().resolve()
    store = Store(root)  # fold the WAL into relif.db so the snapshot is self-contained
    try:
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        store.close()
    proc = runner(["restic", "backup", str(root), "--tag", "relif"], capture_output=True, text=True)
    if proc.returncode != 0:
        return Result(False, False, f"restic backup failed: {(proc.stderr or '').strip()[:500]}")
    return Result(True, False, f"backup done. Suggested policy: {POLICY}")


def restore_test(root, *, runner=subprocess.run, which=shutil.which, env=None) -> Result:
    env = os.environ if env is None else env
    why = _unavailable(env, which)
    if why:
        return Result(True, True, why.replace("backup skipped", "restore-test skipped"))
    root = Path(root).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="relif-restore-") as target:
        proc = runner(["restic", "restore", "latest", "--tag", "relif", "--target", target,
                       "--include", str(root)], capture_output=True, text=True)
        if proc.returncode != 0:
            return Result(False, False, f"restic restore failed: {(proc.stderr or '').strip()[:500]}")
        restored = Path(target) / str(root).lstrip("/")
        try:
            store = Store(restored)
        except Exception as exc:  # noqa: BLE001 - report anything that stops the restored copy opening
            return Result(False, False, f"restored copy does not open: {exc}")
        try:
            report = store.verify()
        finally:
            store.close()
        if not report.ok:
            return Result(False, False, "restored copy fails verify: " + "; ".join(report.problems))
        return Result(True, False, f"restore-test passed: {report.events} events, {report.receipts} receipts, "
                                   f"chain intact")
