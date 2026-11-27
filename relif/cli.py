"""`relif` command line: a thin argparse layer over Store (other front-ends reuse Store directly)."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .backup import backup, restore_test
from .capture import answers_for, log_expense
from .export import export_year
from .money import format_cents, parse_cents
from .store import RelifError, Store, entry_retention
from .verdict import KINDS, LOAN_PARTS, TRI, USES, WORK, decide


def _home(args) -> Path:
    return Path(args.home or os.environ.get("RELIF_HOME") or "~/.relif").expanduser()


def _open(args) -> Store:
    return Store(_home(args))


def cmd_init(args, out):
    with Store.init(_home(args)) as s:
        print(f"RELIF store ready at {s.root}", file=out)


def cmd_property(args, out):
    with _open(args) as s:
        if args.action == "add":
            pid = s.add_property(args.name, address=args.address, rented_pct=args.rented_pct,
                                 acquired_on=args.acquired_on)
            print(f"property #{pid} {args.name}", file=out)
        elif args.action == "dispose":
            s.dispose_property(args.id, args.on)
            print(f"property #{args.id} disposed on {args.on}", file=out)
        else:
            for p in s.properties():
                print(f"#{p['id']}  {p['name']}  {p['address'] or ''}  rented {p['rented_pct']}%"
                      f"{'  disposed ' + p['disposed_on'] if p['disposed_on'] else ''}", file=out)


def cmd_add(args, out):
    try:
        cents = parse_cents(args.amount)
    except ValueError as exc:
        raise RelifError(str(exc)) from None
    with _open(args) as s:
        eid = s.add(amount_cents=cents, date=args.date or date.today().isoformat(), vendor=args.vendor,
                    description=args.description, property_id=args.property, tax_year=args.tax_year,
                    category=args.category, verdict=args.verdict, is_capital=args.capital,
                    cca_class=args.cca_class, filed_late_on=args.filed_late_on, on_hold=args.hold,
                    receipts=args.receipt or [])
        e = s.entry(eid)
        print(f"entry #{eid}  {e['date']}  {e['vendor']}  {format_cents(e['amount_cents'])}"
              f"  keep until {e['retain_until'] or 'no end date'}", file=out)


def print_verdict(v, out):
    print(v.label, file=out)
    print(f"  why: {v.reason}", file=out)
    for note in v.assumptions:
        print(f"  {note}", file=out)
    print(f"  source: {v.citation}", file=out)
    print(f"  {v.disclaimer}", file=out)


def cmd_ask(args, out):
    if args.amount is not None and not args.vendor:
        raise RelifError("--vendor is required with --amount to log the expense")
    with _open(args) as s:
        try:
            v = decide(answers_for(s, args.property, rental_pct=args.rental_pct, kind=args.kind, use=args.use,
                                   available=args.available, loan_part=args.loan_part, work=args.work,
                                   personal_pct=args.personal_pct, short_term=args.short_term,
                                   str_compliant=args.str_compliant, profit=args.profit))
        except ValueError as exc:
            raise RelifError(str(exc)) from None
        print_verdict(v, out)
        if args.amount is None:
            return
        try:
            cents = parse_cents(args.amount)
        except ValueError as exc:
            raise RelifError(str(exc)) from None
        eid = log_expense(s, v, amount_cents=cents, date=args.date or date.today().isoformat(),
                          vendor=args.vendor, description=args.description, property_id=args.property,
                          receipts=args.receipt or [])
        print(f"entry #{eid} logged  {format_cents(cents)}  {args.vendor}", file=out)


def cmd_list(args, out):
    with _open(args) as s:
        rows = s.entries(year=args.year, property_id=args.property, search=args.search,
                         include_voided=not args.active)
        for e in rows:
            n = len(s.receipts_for(e["id"]))
            flag = "  VOID" if e["voided"] else ""
            print(f"#{e['id']:<4} {e['date']}  {format_cents(e['amount_cents']):>10}  {e['vendor']}"
                  f"  {e['category'] or ''}  [{n} receipt{'s' if n != 1 else ''}]{flag}", file=out)
        print(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}", file=out)


def cmd_void(args, out):
    with _open(args) as s:
        s.void(args.id, args.reason)
        print(f"entry #{args.id} voided ({args.reason})", file=out)


def cmd_attach(args, out):
    with _open(args) as s:
        sha = s.attach(args.id, args.receipt)
        print(f"receipt {sha[:12]} attached to entry #{args.id}", file=out)


def cmd_verify(args, out):
    with _open(args) as s:
        r = s.verify()
    for year, (n, rc) in sorted(r.years.items()):
        print(f"{year}: {n} entries, {rc} receipts", file=out)
    anchored = f"last anchored {r.last_anchor['ts'][:10]}" if r.last_anchor else "never anchored (run export)"
    if r.ok:
        print(f"OK: {r.events} events, {r.receipts} receipt files, chain intact, {anchored}", file=out)
        return 0
    for p in r.problems:
        print(f"PROBLEM: {p}", file=out)
    print(f"FAILED: {len(r.problems)} problem(s), {anchored}", file=out)
    return 1


def cmd_rebuild(args, out):
    with _open(args) as s:
        s.rebuild()
    print("derived tables rebuilt from the event log (history unchanged); run `relif verify`", file=out)


def cmd_export(args, out):
    with _open(args) as s:
        path = export_year(s, args.year, force=args.force)
    print(f"bundle written: {path}", file=out)


def cmd_retention(args, out):
    today = date.fromisoformat(args.today) if args.today else date.today()
    with _open(args) as s:
        disposed = {p["id"]: p["disposed_on"] for p in s.properties()}
        eligible, kept = [], 0
        for e in s.entries():
            r = entry_retention(e, disposed.get(e["property_id"]))
            if r.eligible_for_deletion(today):
                eligible.append((e, r))
            else:
                kept += 1
    for e, r in eligible:
        print(f"#{e['id']:<4} {e['date']}  {e['vendor']}  kept until {r.retain_until}  ({r.rule})", file=out)
    print(f"{len(eligible)} entr{'y' if len(eligible) == 1 else 'ies'} past retention as of {today}; "
          f"{kept} must be kept. RELIF never deletes automatically. Not tax advice.", file=out)


def cmd_serve(args, out):
    from .web import lan_address, make_server, pairing_token
    if args.max_mb < 1:
        raise RelifError("--max-mb must be at least 1")
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    with _open(args):  # fail fast before touching the store folder
        pass
    token = pairing_token(_home(args), rotate=args.new_token)
    server = make_server(_home(args), host=host, port=args.port, token=token, max_bytes=args.max_mb * 1024 * 1024)
    shown = lan_address() if args.lan else "127.0.0.1"
    print(f"RELIF capture page on http://{shown}:{server.server_address[1]}/", file=out)
    print(f"Pair a device (keep this link private): http://{shown}:{server.server_address[1]}/pair?t={server.token}",
          file=out)
    if args.lan:
        print("Listening on your local network over plain HTTP: use it on trusted Wi-Fi or Tailscale only.",
              file=out)
    print("Ctrl-C to stop.", file=out, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _result(res, out):
    print(("SKIPPED: " if res.skipped else "") + res.message, file=out)
    return 0 if res.ok else 1


def cmd_backup(args, out):
    return _result(backup(_home(args)), out)


def cmd_restore_test(args, out):
    return _result(restore_test(_home(args)), out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="relif", description="Tamper-evident 6-year ledger for rental expenses.")
    p.add_argument("--home", help="store folder (default $RELIF_HOME or ~/.relif)")
    p.add_argument("--version", action="version", version=f"relif {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the store").set_defaults(func=cmd_init)

    pp = sub.add_parser("property", help="add, dispose or list properties")
    psub = pp.add_subparsers(dest="action", required=True)
    pa = psub.add_parser("add")
    pa.add_argument("name")
    pa.add_argument("--address")
    pa.add_argument("--rented-pct", type=int, default=100)
    pa.add_argument("--acquired-on")
    pd = psub.add_parser("dispose")
    pd.add_argument("id", type=int)
    pd.add_argument("--on", required=True, help="disposal date YYYY-MM-DD")
    psub.add_parser("list")
    pp.set_defaults(func=cmd_property)

    a = sub.add_parser("add", help="record an expense")
    a.add_argument("amount", help="e.g. 42.10")
    a.add_argument("vendor")
    a.add_argument("--date", help="YYYY-MM-DD (default today)")
    a.add_argument("--description", "-d")
    a.add_argument("--property", type=int)
    a.add_argument("--tax-year", type=int)
    a.add_argument("--category", help="e.g. 'Repairs and maintenance (8960)'")
    a.add_argument("--verdict")
    a.add_argument("--capital", action="store_true", help="capital item (kept until 6y after disposal)")
    a.add_argument("--cca-class")
    a.add_argument("--filed-late-on", help="date the return for this year was filed late")
    a.add_argument("--hold", action="store_true", help="under objection/appeal: keep with no end date")
    a.add_argument("--receipt", "-r", action="append", help="receipt file (repeatable)")
    a.set_defaults(func=cmd_add)

    q = sub.add_parser("ask", help="is this deductible? (add --amount and --vendor to log it too)")
    q.add_argument("--kind", choices=KINDS, help="what was bought")
    q.add_argument("--use", choices=USES, default="rental",
                   help="for a rented unit only, shared with your own unit, or your own unit")
    q.add_argument("--property", type=int)
    q.add_argument("--rental-pct", type=int, help="rented share for shared use (default: the property's)")
    q.add_argument("--available", choices=TRI, default="yes", help="property available for rent then")
    q.add_argument("--loan-part", choices=LOAN_PARTS, help="for loan payments")
    q.add_argument("--work", choices=WORK, help="for repairs: restores, improves or is a separate asset")
    q.add_argument("--personal-pct", type=int, default=0, help="personal share of a mixed purchase")
    q.add_argument("--short-term", action="store_true", help="short-term rental (under 90 days)")
    q.add_argument("--str-compliant", choices=TRI, help="short-term rental meets local licensing")
    q.add_argument("--profit", choices=TRI, default="yes", help="reasonable expectation of profit")
    q.add_argument("--amount", help="log it: e.g. 42.10")
    q.add_argument("--vendor")
    q.add_argument("--date", help="YYYY-MM-DD (default today)")
    q.add_argument("--description", "-d")
    q.add_argument("--receipt", "-r", action="append", help="receipt file (repeatable)")
    q.set_defaults(func=cmd_ask)

    l = sub.add_parser("list", help="list entries")
    l.add_argument("--year", type=int)
    l.add_argument("--property", type=int)
    l.add_argument("--search")
    l.add_argument("--active", action="store_true", help="hide voided entries")
    l.set_defaults(func=cmd_list)

    v = sub.add_parser("void", help="void an entry (corrections are void + re-add)")
    v.add_argument("id", type=int)
    v.add_argument("--reason", required=True)
    v.set_defaults(func=cmd_void)

    at = sub.add_parser("attach", help="attach a receipt to an existing entry")
    at.add_argument("id", type=int)
    at.add_argument("receipt")
    at.set_defaults(func=cmd_attach)

    sub.add_parser("verify", help="re-walk the hash chain and re-hash every receipt").set_defaults(func=cmd_verify)

    sub.add_parser("rebuild", help="rebuild entries/receipts tables from the event log") \
        .set_defaults(func=cmd_rebuild)

    e = sub.add_parser("export", help="write the yearly audit bundle")
    e.add_argument("--year", type=int, required=True)
    e.add_argument("--force", action="store_true", help="replace an existing bundle")
    e.set_defaults(func=cmd_export)

    r = sub.add_parser("retention", help="list entries past their CRA retention date")
    r.add_argument("--today", help="pretend today is YYYY-MM-DD")
    r.set_defaults(func=cmd_retention)

    sv = sub.add_parser("serve", help="local phone capture page (localhost unless --lan)")
    sv.add_argument("--lan", action="store_true", help="listen on the local network (home Wi-Fi / Tailscale)")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--max-mb", type=int, default=20, help="upload size limit in MB")
    sv.add_argument("--new-token", action="store_true", help="rotate the pairing token (unpairs every device)")
    sv.set_defaults(func=cmd_serve)

    sub.add_parser("backup", help="restic backup of the store (if configured)").set_defaults(func=cmd_backup)
    sub.add_parser("restore-test", help="restore the latest backup to a temp dir and verify it") \
        .set_defaults(func=cmd_restore_test)
    return p


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out) or 0
    except RelifError as exc:
        print(f"relif: {exc}", file=sys.stderr)
        return 2
    except (sqlite3.Error, OSError) as exc:
        print(f"relif: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def main_exit() -> None:
    sys.exit(main())
