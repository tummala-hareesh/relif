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
import hashlib
import re
import shutil
from datetime import date
from pathlib import Path

from .money import format_cents
from .store import ANCHORS, RelifError, Store, sha256_file

DISCLAIMER = ("RELIF is a record-keeping tool, not tax advice. It never files anything with the CRA. "
              "Check categories and deductibility with the CRA guide T4036 or an accountant.")

LEDGER_COLUMNS = ["entry_id", "date", "tax_year", "property", "vendor", "description", "amount",
                  "category", "verdict", "is_capital", "cca_class", "voided", "void_reason",
                  "retain_until", "retention_rule", "receipts"]


def _slug(text: str, limit: int = 30) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:limit].strip("-") or "x"


def export_year(store: Store, year: int, *, force: bool = False) -> Path:
    out = store.root / "exports" / str(year)
    if out.exists() and not force:
        raise RelifError(f"{out} already exists (use --force to replace it)")
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
                    raise RelifError(f"receipt missing for entry #{e['id']}: run `relif verify`")
                shutil.copyfile(src, tmp / "receipts" / name)
                names.append(name)
            w.writerow([e["id"], e["date"], e["tax_year"], props.get(e["property_id"], ""), e["vendor"],
                        e["description"] or "", format_cents(e["amount_cents"]), e["category"] or "",
                        e["verdict"] or "", "yes" if e["is_capital"] else "no", e["cca_class"] or "",
                        "yes" if e["voided"] else "no", e["void_reason"] or "",
                        e["retain_until"] or "keep (no end date)", e["retention_rule"] or "", " ".join(names)])

    totals = {}
    for e in entries:
        if e["voided"] or e["is_capital"]:
            continue
        key = (props.get(e["property_id"], ""), e["category"] or "uncategorised")
        totals[key] = totals.get(key, 0) + e["amount_cents"]
    with open(tmp / "t776_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["property", "category", "total"])
        for (prop, cat), cents in sorted(totals.items()):
            w.writerow([prop, cat, format_cents(cents)])

    capital = [e for e in store.entries(include_voided=False) if e["is_capital"] and e["tax_year"] <= year]
    with open(tmp / "capital_register.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_id", "date", "tax_year", "property", "vendor", "description", "amount",
                    "cca_class", "retain_until"])
        for e in capital:
            w.writerow([e["id"], e["date"], e["tax_year"], props.get(e["property_id"], ""), e["vendor"],
                        e["description"] or "", format_cents(e["amount_cents"]), e["cca_class"] or "",
                        e["retain_until"] or "keep (no end date)"])

    anchor = store.anchor(f"export {year}")
    shutil.copyfile(store.root / ANCHORS, tmp / "anchors.log")

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
        "Files: ledger.csv (all entries), t776_summary.csv (current expenses by property and category),",
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

    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    return out


def check_manifest(bundle: Path) -> list:
    """Problems found re-hashing a bundle against its MANIFEST.sha256 (empty list = intact)."""
    problems, listed = [], set()
    for line in (bundle / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, _, rel = line.partition("  ")
        listed.add(rel)
        path = bundle / rel
        if not path.exists():
            problems.append(f"missing: {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"changed: {rel}")
    for path in bundle.rglob("*"):
        rel = path.relative_to(bundle).as_posix()
        if path.is_file() and rel != "MANIFEST.sha256" and rel not in listed:
            problems.append(f"not in manifest: {rel}")
    return problems
