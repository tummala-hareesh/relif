"""The Store: an append-only, hash-chained event ledger plus a content-addressed receipt folder.

Layout of a RELIF home (default ``~/.relif``)::

    relif.db                          SQLite (WAL), PRAGMA user_version = schema version
    receipts/ab/cd/<sha256>.<ext>     receipts, stored once by content hash, read-only
    exports/<year>/                   yearly bundles (see relif.export)
    anchors.log                       chain heads (JSON lines), copied into every export

Every change is an event. ``events`` rows cannot be updated or deleted (triggers abort), and each
row's hash covers the previous row's hash, so ``verify`` can prove the history is intact. Tail
truncation is caught against the heads recorded in ``anchors.log``. The ``entries``,
``properties`` and ``receipts`` tables are derived state, rebuilt from events.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from .retention import retention

SCHEMA_VERSION = 1
GENESIS = "0" * 64
DB_NAME = "relif.db"
ANCHORS = "anchors.log"
MIN_YEAR, MAX_YEAR = 1900, 9000
MAX_CENTS = 2 ** 63 - 1
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_EXT_RE = re.compile(r"^[a-z0-9]{0,10}$")

_SCHEMA = """
CREATE TABLE events(
    seq INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    entry_id INTEGER,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""

_DERIVED = """
CREATE TABLE properties(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    rented_pct INTEGER,
    acquired_on TEXT,
    disposed_on TEXT
);
CREATE TABLE entries(
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    vendor TEXT NOT NULL,
    description TEXT,
    property_id INTEGER,
    tax_year INTEGER NOT NULL,
    category TEXT,
    verdict TEXT,
    is_capital INTEGER NOT NULL DEFAULT 0,
    cca_class TEXT,
    filed_late_on TEXT,
    on_hold INTEGER NOT NULL DEFAULT 0,
    retain_until TEXT,
    retention_rule TEXT,
    voided INTEGER NOT NULL DEFAULT 0,
    void_reason TEXT
);
CREATE TABLE receipts(
    sha256 TEXT PRIMARY KEY,
    ext TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    original_name TEXT,
    added_at TEXT NOT NULL
);
CREATE TABLE entry_receipts(
    entry_id INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(entry_id, sha256)
);
CREATE INDEX entries_year_property ON entries(tax_year, property_id);
CREATE INDEX entries_category ON entries(category);
CREATE INDEX entries_retain_until ON entries(retain_until);
"""

_DERIVED_TABLES = ("properties", "entries", "receipts", "entry_receipts")

ENTRY_FIELDS = ("id", "date", "amount_cents", "vendor", "description", "property_id", "tax_year",
                "category", "verdict", "is_capital", "cca_class", "filed_late_on", "on_hold")
PROPERTY_FIELDS = ("name", "address", "rented_pct", "acquired_on", "disposed_on")


class RelifError(Exception):
    """A user-facing error (bad input, missing store, ...)."""


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(prev_hash: str, seq: int, ts: str, kind: str, entry_id, payload) -> str:
    body = canonical_json([seq, ts, kind, entry_id, payload])
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(value, name: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise RelifError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


@dataclass
class VerifyReport:
    events: int = 0
    head_seq: int = 0
    head_hash: str = GENESIS
    receipts: int = 0
    last_anchor: Optional[dict] = None
    problems: list = field(default_factory=list)
    years: dict = field(default_factory=dict)  # tax_year -> (entries, receipts)

    @property
    def ok(self) -> bool:
        return not self.problems


class Store:
    def __init__(self, root, *, clock: Callable[[], datetime] = _utcnow):
        self.root = Path(root).expanduser()
        self.clock = clock
        db = self.root / DB_NAME
        if not db.exists():
            raise RelifError(f"no RELIF store at {self.root} (run `relif init` first)")
        self.conn = self._connect(db)
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            self.conn.close()
            raise RelifError(f"store schema version {version}, this RELIF expects {SCHEMA_VERSION}")

    # ---- lifecycle -----------------------------------------------------------------------
    @classmethod
    def init(cls, root, *, clock: Callable[[], datetime] = _utcnow) -> "Store":
        root = Path(root).expanduser()
        db = root / DB_NAME
        if not db.exists():
            (root / "receipts").mkdir(parents=True, exist_ok=True)
            (root / "exports").mkdir(exist_ok=True)
            conn = cls._connect(db)
            with conn:
                conn.executescript(_SCHEMA + _DERIVED)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.close()
        return cls(root, clock=clock)

    @staticmethod
    def _connect(db: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _tx(self):
        """A write transaction that takes the lock before reading head / next ids."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.rollback()
            raise
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- event log -----------------------------------------------------------------------
    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")

    def head(self) -> tuple[int, str]:
        row = self.conn.execute("SELECT seq, row_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return (row["seq"], row["row_hash"]) if row else (0, GENESIS)

    def _append(self, kind: str, entry_id, payload: dict) -> int:
        seq, prev = self.head()
        seq += 1
        ts = self._now()
        payload = json.loads(canonical_json(payload))
        row_hash = event_hash(prev, seq, ts, kind, entry_id, payload)
        self.conn.execute(
            "INSERT INTO events(seq, ts, kind, entry_id, payload_json, prev_hash, row_hash)"
            " VALUES (?,?,?,?,?,?,?)",
            (seq, ts, kind, entry_id, canonical_json(payload), prev, row_hash))
        _apply(self.conn, kind, entry_id, payload, ts)
        return seq

    def events(self) -> list:
        return self.conn.execute("SELECT * FROM events ORDER BY seq").fetchall()

    # ---- properties ----------------------------------------------------------------------
    def add_property(self, name: str, *, address=None, rented_pct=100, acquired_on=None) -> int:
        if not name:
            raise RelifError("property name is required")
        rented_pct = int(rented_pct)
        if not 0 <= rented_pct <= 100:
            raise RelifError("rented_pct must be between 0 and 100")
        acquired = _iso_date(acquired_on, "acquired_on")
        with self._tx():
            pid = (self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM properties").fetchone()[0]) + 1
            self._append("property.changed", None, {"id": pid, "name": name, "address": address,
                                                    "rented_pct": rented_pct, "acquired_on": acquired,
                                                    "disposed_on": None})
        return pid

    def dispose_property(self, property_id: int, disposed_on) -> None:
        prop = self.property(property_id)
        day = _iso_date(disposed_on, "disposed_on")
        if day is None:
            raise RelifError("disposal date is required")
        if int(day[:4]) > MAX_YEAR:
            raise RelifError(f"disposal year must be at most {MAX_YEAR}")
        if prop["acquired_on"] and day < prop["acquired_on"]:
            raise RelifError(f"disposal date {day} is before acquisition {prop['acquired_on']}")
        payload = {"id": prop["id"], "disposed_on": day}
        with self._tx():
            self._append("property.changed", None, payload)

    def property(self, property_id: int):
        row = self.conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if row is None:
            raise RelifError(f"no property #{property_id}")
        return row

    def properties(self) -> list:
        return self.conn.execute("SELECT * FROM properties ORDER BY id").fetchall()

    # ---- entries -------------------------------------------------------------------------
    def add(self, *, amount_cents: int, date, vendor: str, description: str = None,
            property_id: int = None, tax_year: int = None, category: str = None, verdict: str = None,
            is_capital: bool = False, cca_class: str = None, filed_late_on=None, on_hold: bool = False,
            receipts: Iterable = ()) -> int:
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
            raise RelifError("amount must be integer cents")
        if abs(amount_cents) > MAX_CENTS:
            raise RelifError("amount is too large")
        if not vendor:
            raise RelifError("vendor is required")
        day = _iso_date(date, "date")
        if day is None:
            raise RelifError("date is required")
        if property_id is not None:
            self.property(property_id)
        paths = [Path(p).expanduser() for p in receipts]
        for p in paths:
            if not p.is_file():
                raise RelifError(f"receipt file not found: {p}")
        year = int(tax_year) if tax_year is not None else int(day[:4])
        if not MIN_YEAR <= year <= MAX_YEAR:
            raise RelifError(f"tax year must be between {MIN_YEAR} and {MAX_YEAR}")
        payload = {
            "date": day, "amount_cents": amount_cents, "vendor": vendor,
            "description": description, "property_id": property_id,
            "tax_year": year,
            "category": category, "verdict": verdict, "is_capital": bool(is_capital), "cca_class": cca_class,
            "filed_late_on": _iso_date(filed_late_on, "filed_late_on"), "on_hold": bool(on_hold),
        }
        stored = [self._store_receipt(p) for p in paths]  # files first: content-addressed, idempotent
        with self._tx():
            eid = (self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM entries").fetchone()[0]) + 1
            self._append("entry.created", eid, {"id": eid, **payload})
            for receipt in stored:
                self._append_receipt(eid, receipt)
        return eid

    def _append_receipt(self, entry_id: int, stored) -> None:
        sha, ext, size, name = stored
        self._append("receipt.attached", entry_id, {"sha256": sha, "ext": ext, "bytes": size, "original_name": name})

    def attach(self, entry_id: int, receipt) -> str:
        self.entry(entry_id)
        path = Path(receipt).expanduser()
        if not path.is_file():
            raise RelifError(f"receipt file not found: {path}")
        stored = self._store_receipt(path)
        with self._tx():
            self._append_receipt(entry_id, stored)
        return stored[0]

    def void(self, entry_id: int, reason: str) -> None:
        entry = self.entry(entry_id)
        if entry["voided"]:
            raise RelifError(f"entry #{entry_id} is already voided")
        if not reason:
            raise RelifError("a reason is required to void an entry")
        with self._tx():
            self._append("entry.voided", entry_id, {"reason": reason})

    def entry(self, entry_id: int):
        row = self.conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise RelifError(f"no entry #{entry_id}")
        return row

    def entries(self, *, year: int = None, property_id: int = None, search: str = None,
                include_voided: bool = True) -> list:
        sql, args = "SELECT * FROM entries WHERE 1=1", []
        if year is not None:
            sql += " AND tax_year = ?"
            args.append(year)
        if property_id is not None:
            sql += " AND property_id = ?"
            args.append(property_id)
        if search:
            sql += r" AND (vendor LIKE ? ESCAPE '\' OR description LIKE ? ESCAPE '\')"
            pattern = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args += [f"%{pattern}%"] * 2
        if not include_voided:
            sql += " AND voided = 0"
        return self.conn.execute(sql + " ORDER BY date, id", args).fetchall()

    def receipts_for(self, entry_id: int) -> list:
        return self.conn.execute(
            "SELECT r.* FROM receipts r JOIN entry_receipts er ON er.sha256 = r.sha256"
            " WHERE er.entry_id = ? ORDER BY r.added_at, r.sha256", (entry_id,)).fetchall()

    # ---- receipts ------------------------------------------------------------------------
    def receipt_path(self, sha: str, ext: str) -> Path:
        if not _SHA_RE.match(sha or "") or not _EXT_RE.match(ext or ""):
            raise RelifError(f"invalid receipt id {sha!r}.{ext!r}")
        return self.root / "receipts" / sha[:2] / sha[2:4] / (sha + (f".{ext}" if ext else ""))

    def _store_receipt(self, src: Path):
        sha = sha256_file(src)
        ext = src.suffix.lstrip(".").lower()
        if not _EXT_RE.match(ext):
            ext = ""
        dest = self.receipt_path(sha, ext)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".incoming-")
            os.close(fd)
            shutil.copyfile(src, tmp)
            if sha256_file(Path(tmp)) != sha:
                os.unlink(tmp)
                raise RelifError(f"receipt changed while copying: {src}")
            os.chmod(tmp, 0o444)
            os.replace(tmp, dest)
        return sha, ext, src.stat().st_size, src.name

    # ---- anchors -------------------------------------------------------------------------
    def anchor_record(self, note: str = "") -> dict:
        seq, head = self.head()
        return {"ts": self._now(), "seq": seq, "head": head, "note": note}

    def anchor(self, note: str = "", record: Optional[dict] = None) -> dict:
        record = record or self.anchor_record(note)
        with open(self.root / ANCHORS, "a", encoding="utf-8") as f:
            f.write(canonical_json(record) + "\n")
        return record

    def anchors(self) -> list:
        return _read_anchors(self.root / ANCHORS)

    # ---- verify --------------------------------------------------------------------------
    def verify(self) -> VerifyReport:
        report = VerifyReport()
        prev, expected_seq, hashes = GENESIS, 1, {}
        for row in self.events():
            report.events += 1
            seq = row["seq"]
            if seq != expected_seq:
                report.problems.append(f"chain gap: expected event {expected_seq}, found {seq} (events deleted)")
            if row["prev_hash"] != prev:
                report.problems.append(f"chain broken at event {seq}: prev_hash does not match event before it")
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                payload = row["payload_json"]
            if event_hash(row["prev_hash"], seq, row["ts"], row["kind"], row["entry_id"], payload) != row["row_hash"]:
                report.problems.append(f"event {seq} was modified (hash mismatch)")
            prev, expected_seq = row["row_hash"], seq + 1
            hashes[seq] = row["row_hash"]
            report.head_seq, report.head_hash = seq, row["row_hash"]

        live = self.anchors()
        live_keys = {(a.get("seq"), a.get("head")) for a in live if "invalid" not in a}
        exported = []
        for copy in sorted((self.root / "exports").glob("*/anchors.log")):
            for a in _read_anchors(copy):
                if "invalid" in a:
                    report.problems.append(f"{copy.parent.name}/anchors.log line {a['invalid']} is not a valid anchor")
                elif (a["seq"], a["head"]) not in live_keys:
                    report.problems.append(
                        f"anchor from export {copy.parent.name} (event {a['seq']}) is missing from anchors.log")
                    exported.append(a)
        for a in live + exported:
            if "invalid" in a:
                report.problems.append(f"anchors.log line {a['invalid']} is not a valid anchor")
                continue
            report.last_anchor = a
            if a["seq"] > report.head_seq:
                report.problems.append(
                    f"chain truncated: ends at event {report.head_seq}, but anchor {a['ts']} recorded event {a['seq']}")
            elif a["seq"] > 0 and hashes.get(a["seq"]) != a["head"]:
                report.problems.append(f"event {a['seq']} does not match the head anchored at {a['ts']}")

        for r in self.conn.execute("SELECT * FROM receipts ORDER BY sha256"):
            report.receipts += 1
            try:
                path = self.receipt_path(r["sha256"], r["ext"])
            except RelifError as exc:
                report.problems.append(str(exc))
                continue
            if not path.exists():
                report.problems.append(f"receipt missing: {r['original_name']} ({r['sha256'][:12]})")
            elif sha256_file(path) != r["sha256"]:
                report.problems.append(f"receipt modified: {r['original_name']} ({r['sha256'][:12]})")

        drift, replay_errors = self._derived_drift(with_errors=True)
        report.problems.extend(replay_errors)
        if drift:
            report.problems.append(f"derived tables differ from the event log: {', '.join(drift)}"
                                   " (`relif rebuild` restores them from the log)")

        for row in self.conn.execute(
                "SELECT e.tax_year, COUNT(DISTINCT e.id) AS n, COUNT(er.sha256) AS r FROM entries e"
                " LEFT JOIN entry_receipts er ON er.entry_id = e.id GROUP BY e.tax_year ORDER BY e.tax_year"):
            report.years[row["tax_year"]] = (row["n"], row["r"])
        return report

    def _derived_drift(self, with_errors: bool = False):
        """Replay the event log into memory and compare with the stored derived tables."""
        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        mem.executescript(_DERIVED)
        errors = []
        for row in self.events():
            try:
                _apply(mem, row["kind"], row["entry_id"], json.loads(row["payload_json"]), row["ts"])
            except Exception as exc:  # noqa: BLE001 - any failure here is evidence, report it
                errors.append(f"event {row['seq']} cannot be applied: {exc}")
        drift = []
        for table in _DERIVED_TABLES:
            mine = [tuple(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
            theirs = [tuple(r) for r in mem.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
            if mine != theirs:
                drift.append(table)
        mem.close()
        return (drift, errors) if with_errors else drift

    def rebuild(self) -> None:
        """Recreate the derived tables from the event log (repairs drift; history is untouched)."""
        with self.conn:
            for table in _DERIVED_TABLES:
                self.conn.execute(f"DELETE FROM {table}")
            for row in self.events():
                _apply(self.conn, row["kind"], row["entry_id"], json.loads(row["payload_json"]), row["ts"])


# ---- applying events to derived state --------------------------------------------------------
def _apply(conn: sqlite3.Connection, kind: str, entry_id, payload: dict, ts: str) -> None:
    if kind == "entry.created":
        cols = ENTRY_FIELDS
        vals = [payload.get(c) for c in cols]
        vals = [int(v) if isinstance(v, bool) else v for v in vals]
        conn.execute(f"INSERT INTO entries({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        _refresh_retention(conn, "id = ?", (payload["id"],))
    elif kind == "entry.voided":
        conn.execute("UPDATE entries SET voided = 1, void_reason = ? WHERE id = ?", (payload["reason"], entry_id))
    elif kind == "receipt.attached":
        if not _SHA_RE.match(str(payload["sha256"])) or not _EXT_RE.match(str(payload["ext"])):
            raise RelifError("receipt.attached has an invalid sha256 or extension")
        conn.execute("INSERT OR IGNORE INTO receipts(sha256, ext, bytes, original_name, added_at) VALUES (?,?,?,?,?)",
                     (payload["sha256"], payload["ext"], payload["bytes"], payload.get("original_name"), ts))
        conn.execute("INSERT OR IGNORE INTO entry_receipts(entry_id, sha256) VALUES (?,?)",
                     (entry_id, payload["sha256"]))
    elif kind == "property.changed":
        pid = payload["id"]
        exists = conn.execute("SELECT 1 FROM properties WHERE id = ?", (pid,)).fetchone()
        changes = {k: payload[k] for k in PROPERTY_FIELDS if k in payload}
        if exists:
            if changes:
                sets = ", ".join(f"{k} = ?" for k in changes)
                conn.execute(f"UPDATE properties SET {sets} WHERE id = ?", (*changes.values(), pid))
        else:
            cols = ("id", *changes)
            conn.execute(f"INSERT INTO properties({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         (pid, *changes.values()))
        _refresh_retention(conn, "property_id = ?", (pid,))
    else:
        raise RelifError(f"unknown event kind {kind!r}")


def _refresh_retention(conn: sqlite3.Connection, where: str, args: tuple) -> None:
    rows = conn.execute(
        "SELECT e.id, e.tax_year, e.filed_late_on, e.on_hold, e.is_capital, p.disposed_on FROM entries e"
        f" LEFT JOIN properties p ON p.id = e.property_id WHERE e.{where}", args).fetchall()
    for r in rows:
        ret = entry_retention(r, r["disposed_on"])
        conn.execute("UPDATE entries SET retain_until = ?, retention_rule = ? WHERE id = ?",
                     (ret.retain_until.isoformat() if ret.retain_until else None, ret.rule, r["id"]))


def entry_retention(entry, disposed_on: Optional[str] = None):
    """The full Retention (with citation) for an entries row."""
    return retention(
        entry["tax_year"],
        filed_late_on=date.fromisoformat(entry["filed_late_on"]) if entry["filed_late_on"] else None,
        on_hold=bool(entry["on_hold"]), is_capital=bool(entry["is_capital"]),
        disposed_on=date.fromisoformat(disposed_on) if disposed_on else None)


def _read_anchors(path: Path) -> list:
    """Anchor records from an anchors.log; malformed lines become {"invalid": line_number}."""
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            a = json.loads(line)
        except json.JSONDecodeError:
            a = None
        ok = (isinstance(a, dict) and isinstance(a.get("seq"), int) and not isinstance(a.get("seq"), bool)
              and isinstance(a.get("ts"), str) and isinstance(a.get("head"), str) and _SHA_RE.match(a["head"]))
        out.append(a if ok else {"invalid": n})
    return out
