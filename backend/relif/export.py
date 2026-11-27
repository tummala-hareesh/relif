"""Yearly audit bundle: a self-explaining folder an accountant can read without RELIF.

exports/<year>/
    ledger.csv            every entry of the tax year (voided ones marked, never dropped)
    t776_summary.csv      totals per property and category (current expenses, not voided)
    capital_register.csv  capital items up to and including the year
    receipts/             receipt copies named <date>_<vendor>_<amount>_<entry>.<ext>
    anchors.log           chain heads, including the one written by this export
    README.txt            what this is, retention, chain head, disclaimer
    MANIFEST.sha256       sha256 of every file above (check with `sha256sum -c MANIFEST.sha256`)

Writing a bundle anchors the current chain head in anchors.log, so the export doubles as
tamper evidence for the ledger.
"""
from __future__ import annotations

import csv
import re
import shutil
from datetime import date
from pathlib import Path

from .money import format_cents
from .store import ANCHORS, RelifError, Store, canonical_json, sha256_file

DISCLAIMER = ("RELIF is a record-keeping tool, not tax advice. It never files anything with the CRA. "
              "Check categories and deductibility with the CRA guide T4036 or an accountant.")

NO_END_DATE = "keep (no end date)"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    """Text cell safe to open in a spreadsheet (no formula injection from vendor/description)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


LEDGER_COLUMNS = ["entry_id", "date", "tax_year", "property", "vendor", "description", "amount",
                  "category", "verdict", "is_capital", "cca_class", "voided", "void_reason",
                  "retain_until", "retention_rule", "receipts", "deductible_pct"]


def _slug(text: str, limit: int = 30) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:limit].strip("-") or "x"


def export_year(store: Store, year: int, *, force: bool = False, allow_unverified: bool = False) -> Path:
    out = store.root / "exports" / str(year)
    if out.exists() and not force:
        raise RelifError(f"{out} already exists (use --force to replace it)")
    if not allow_unverified:
        report = store.verify()
        if not report.ok:
            raise RelifError("ledger does not verify, refusing to export: " + "; ".join(report.problems))
    tmp = out.with_name(f".{year}.partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "receipts").mkdir(parents=True)

    props = {p["id"]: p["name"] for p in store.properties()}
    entries = store.entries(year=year)

    used = set()
    with open(tmp / "ledger.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(LEDGER_COLUMNS)
        for e in entries:
            names = []
            for r in store.receipts_for(e["id"]):
                base = f"{e['date']}_{_slug(e['vendor'])}_{format_cents(e['amount_cents'])}_{e['id']}"
                name, n = f"{base}.{r['ext']}" if r["ext"] else base, 1
                while name in used:
                    n += 1
                    name = f"{base}-{n}.{r['ext']}" if r["ext"] else f"{base}-{n}"
                used.add(name)
                src = store.receipt_path(r["sha256"], r["ext"])
                if not src.exists():
                    shutil.rmtree(tmp)
                    raise RelifError(f"receipt missing for entry #{e['id']}: run `relif verify`")
                dest = tmp / "receipts" / name
                shutil.copyfile(src, dest)
                if sha256_file(dest) != r["sha256"]:
                    shutil.rmtree(tmp)
                    raise RelifError(f"receipt for entry #{e['id']} does not match its hash: run `relif verify`")
                names.append(name)
            w.writerow([e["id"], e["date"], e["tax_year"], _cell(props.get(e["property_id"])), _cell(e["vendor"]),
                        _cell(e["description"]), format_cents(e["amount_cents"]), _cell(e["category"]),
                        _cell(e["verdict"]), "yes" if e["is_capital"] else "no", _cell(e["cca_class"]),
                        "yes" if e["voided"] else "no", _cell(e["void_reason"]),
                        e["retain_until"] or NO_END_DATE, e["retention_rule"] or "", " ".join(names),
                        "" if e["deductible_pct"] is None else e["deductible_pct"]])

    totals = {}  # (property, category) -> [deductible share, receipts total]; no share recorded = full amount
    for e in entries:
        pct = e["deductible_pct"]
        if e["voided"] or e["is_capital"] or pct == 0:
            continue
        cents = e["amount_cents"]
        share = cents if pct is None else (cents * pct + 50) // 100
        key = (props.get(e["property_id"], ""), e["category"] or "uncategorised")
        t = totals.setdefault(key, [0, 0])
        t[0] += share
        t[1] += cents
    with open(tmp / "t776_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["property", "category", "total", "receipts_total"])
        for (prop, cat), (share, full) in sorted(totals.items()):
            w.writerow([_cell(prop), _cell(cat), format_cents(share), format_cents(full)])

    capital = [e for e in store.entries(include_voided=False) if e["is_capital"] and e["tax_year"] <= year]
    with open(tmp / "capital_register.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_id", "date", "tax_year", "property", "vendor", "description", "amount",
                    "cca_class", "retain_until"])
        for e in capital:
            w.writerow([e["id"], e["date"], e["tax_year"], _cell(props.get(e["property_id"])), _cell(e["vendor"]),
                        _cell(e["description"]), format_cents(e["amount_cents"]), _cell(e["cca_class"]),
                        e["retain_until"] or NO_END_DATE])

    anchor = store.anchor_record(f"export {year}")  # written to anchors.log only once the bundle is in place
    live_log = store.root / ANCHORS
    previous = live_log.read_text(encoding="utf-8") if live_log.exists() else ""
    (tmp / "anchors.log").write_text(previous + canonical_json(anchor) + "\n", encoding="utf-8")

    live = [e for e in entries if not e["voided"]]
    until = sorted({e["retain_until"] for e in entries if e["retain_until"]})
    keep_forever = sum(1 for e in entries if not e["retain_until"])
    (tmp / "README.txt").write_text("\n".join([
        f"RELIF records for tax year {year}",
        f"Generated {anchor['ts']}",
        "",
        f"Entries: {len(entries)} ({len(live)} active, {len(entries) - len(live)} voided), "
        f"total active {format_cents(sum(e['amount_cents'] for e in live))}",
        f"Receipts: {len(used)}",
        "",
        "Files: ledger.csv (all entries), t776_summary.csv (deductible share of current expenses by property and category),",
        "capital_register.csv (capital items), receipts/ (copies of receipts), anchors.log.",
        "",
        "Retention: keep this folder until "
        + (until[-1] if until else "further notice")
        + (f"; {keep_forever} item(s) have no end date (capital property or under objection)." if keep_forever else "."),
        "Rule: CRA - 6 years from the end of the tax year, longer if filed late, under objection,",
        "or for capital property (see retention_rule in ledger.csv).",
        "",
        f"Ledger chain head: event {anchor['seq']} sha256 {anchor['head']}",
        "Integrity: run `sha256sum -c MANIFEST.sha256` in this folder; `relif verify` checks the ledger.",
        "",
        DISCLAIMER,
        "",
    ]), encoding="utf-8")

    lines = []
    for path in sorted(p for p in tmp.rglob("*") if p.is_file()):
        lines.append(f"{sha256_file(path)}  {path.relative_to(tmp).as_posix()}")
    (tmp / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    old = out.with_name(f".{year}.old")
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        out.rename(old)  # keep the previous bundle until the new one is in place
    tmp.rename(out)
    store.anchor(record=anchor)
    if old.exists():
        shutil.rmtree(old)
    return out


def check_manifest(bundle: Path) -> list:
    """Problems found re-hashing a bundle against its MANIFEST.sha256 (empty list = intact)."""
    problems, listed = [], set()
    root = bundle.resolve()
    for n, line in enumerate((bundle / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        digest, sep, rel = line.partition(" ")
        rel = rel[1:] if rel[:1] in (" ", "*") else rel  # sha256sum text ("  ") or binary (" *") mode
        if not sep or not rel or len(digest) != 64:
            problems.append(f"MANIFEST line {n} is malformed")
            continue
        path = bundle / rel
        if root not in path.resolve().parents:
            problems.append(f"MANIFEST line {n} points outside the bundle: {rel}")
            continue
        listed.add(rel)
        if not path.is_file():
            problems.append(f"missing: {rel}")
        elif sha256_file(path) != digest:
            problems.append(f"changed: {rel}")
    for path in bundle.rglob("*"):
        rel = path.relative_to(bundle).as_posix()
        if path.is_file() and rel != "MANIFEST.sha256" and rel not in listed:
            problems.append(f"not in manifest: {rel}")
    return problems
