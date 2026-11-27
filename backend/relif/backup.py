"""Backups are delegated to restic; RELIF only runs it and proves a restore works.

restic is optional: without the binary, a repository (RESTIC_REPOSITORY or RESTIC_REPOSITORY_FILE)
or a password source (RESTIC_PASSWORD, RESTIC_PASSWORD_FILE or RESTIC_PASSWORD_COMMAND) these
commands skip cleanly. The suggested retention policy is POLICY below; run it yourself.
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
TIMEOUT = 6 * 60 * 60  # seconds; a stuck restic must not hang forever


@dataclass
class Result:
    ok: bool
    skipped: bool
    message: str


def _unavailable(env, which, action):
    if which("restic") is None:
        return f"restic is not installed; {action} skipped (install restic to enable it)"
    if not (env.get("RESTIC_REPOSITORY") or env.get("RESTIC_REPOSITORY_FILE")):
        return f"no restic repository configured (set RESTIC_REPOSITORY or RESTIC_REPOSITORY_FILE); {action} skipped"
    if not any(env.get(k) for k in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND")):
        return f"no restic password source (set RESTIC_PASSWORD_FILE or similar); {action} skipped"
    return None


def _run(runner, cmd, env):
    try:
        return runner(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=dict(env),
                      timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def backup(root, *, runner=subprocess.run, which=shutil.which, env=None) -> Result:
    env = os.environ if env is None else env
    why = _unavailable(env, which, "backup")
    if why:
        return Result(True, True, why)
    root = Path(root).expanduser().resolve()
    store = Store(root)  # fold the WAL into relif.db so the snapshot is self-contained
    try:
        busy = store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    finally:
        store.close()
    if busy:
        return Result(False, False, "the store is in use by another relif process; try the backup again")
    proc = _run(runner, ["restic", "backup", str(root), "--tag", "relif"], env)
    if proc.returncode != 0:
        return Result(False, False, f"restic backup failed: {(proc.stderr or '').strip()[:500]}")
    return Result(True, False, f"backup done. Suggested policy: {POLICY}")


def restore_test(root, *, runner=subprocess.run, which=shutil.which, env=None) -> Result:
    env = os.environ if env is None else env
    why = _unavailable(env, which, "restore-test")
    if why:
        return Result(True, True, why)
    root = Path(root).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="relif-restore-") as target:
        proc = _run(runner, ["restic", "restore", "latest", "--tag", "relif", "--path", str(root),
                             "--target", target], env)
        if proc.returncode != 0:
            return Result(False, False, f"restic restore failed: {(proc.stderr or '').strip()[:500]}")
        drive = [root.drive.rstrip(":")] if root.drive else []  # restic stores C:\x as /C/x
        restored = Path(target, *drive, *root.parts[1:])
        if Path(target).resolve() not in restored.resolve().parents:
            return Result(False, False, f"restored path {restored} is outside the restore target")
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
