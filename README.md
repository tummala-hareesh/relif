# relif

🧾 RELIF as a private "is this deductible?" assistant plus abridged evidence ledger is a sharper, more useful idea than year-end CSV sorting.
⏳ CRA: keep records 6 years from end of the tax year (from the filing date if filed late; longer if under objection; property acquisition records indefinitely); scans OK only if accurate and to the CGSB standard, so keep originals.
🌳 An 8-question flow (which unit/% → available for rent → type → interest vs principal → current vs capital → personal use → STR compliance → profit motive) ends in current T776 line / CCA class / partial / not deductible / unsure.
💸 Rivals (Landlii, RentalOps, DwellTrack ~$9-10/mo) already capture receipts but don't explain verdicts; free, local-first, cited reasoning is the gap.
✅ Accept: build a standalone Python CLI + SQLite + year export bundle first (Telegram later); test with 5 landlords logging 2 weeks of real purchases.

Idea issue: https://github.com/tummala-hareesh/agent-megastar/issues/2

Ideation notes: [docs/ideation/](docs/ideation/)

## Storage core (v0.1)

A local-first, tamper-evident ledger for rental expenses and receipts that keeps records for the CRA's
6-year window. Python stdlib only. Not tax advice; RELIF never files anything with the CRA.

```bash
python3 -m relif init                                   # creates ~/.relif (or --home / $RELIF_HOME)
python3 -m relif property add "Unit A" --rented-pct 60
python3 -m relif add 42.10 "Hardware Co" --date 2026-03-04 --property 1 \
    --category "Repairs and maintenance (8960)" -r ~/scans/caulk.pdf
python3 -m relif list --year 2026
python3 -m relif void 2 --reason "typo, re-entered as #3"  # corrections never edit in place
python3 -m relif verify                                  # re-walk the hash chain, re-hash receipts
python3 -m relif export --year 2026                      # ~/.relif/exports/2026/ audit bundle
python3 -m relif retention                               # what is past its retain-until date
python3 -m relif rebuild                                 # restore derived tables from the event log
python3 -m relif backup && python3 -m relif restore-test # via restic, if configured
```

- **Ledger:** every change is an append-only, hash-chained event in SQLite; `verify` catches edited or
  deleted rows, a truncated tail (against `anchors.log`), and changed or missing receipt files.
- **Receipts:** stored once by SHA-256 under `receipts/ab/cd/`, read-only.
- **Retention:** each entry carries `retain_until` and the CRA rule that produced it (late filing,
  objection hold, capital property until 6 years after disposal). Nothing is deleted automatically.
- **Export:** `ledger.csv`, `t776_summary.csv`, `capital_register.csv`, renamed receipts, `README.txt`
  and `MANIFEST.sha256` (`sha256sum -c MANIFEST.sha256`). Each export anchors the chain head.
- **Backup:** delegated to [restic](https://restic.net) (`RESTIC_REPOSITORY` + password); suggested
  policy `restic forget --keep-monthly 24 --keep-yearly 10 --dry-run`.

## Ask and capture (v0.2)

```bash
python3 -m relif ask --kind repair --work restores               # cited verdict only
python3 -m relif ask --kind utilities --use shared --property 1 \
    --amount 120 --vendor Hydro -r ~/scans/hydro.pdf               # verdict + ledger entry
python3 -m relif serve                                            # phone capture page on localhost
python3 -m relif serve --lan                                      # home Wi-Fi / Tailscale (opt-in)
```

- **Verdicts** (`relif/verdict.py`): a pure decision tree over the 8 questions (use, availability, kind,
  interest vs principal, current vs capital, personal share, short-term rental compliance, profit). Outcomes:
  current (T776 line), capital (CCA class), partial (rental %), not deductible, unsure (ask an accountant).
  Every verdict cites CRA T4036 and lists its assumptions. Not tax advice.
- **Capture page**: one mobile form (amount, vendor, date, property, receipt photo, a few answers) that logs the
  entry and shows the verdict, plus a read-only recent-entries page with the chain status. Stdlib only.
  Binds `127.0.0.1` unless `--lan`; it prints a pairing link (cookie; the token is kept in `serve.token`,
  mode 0600, so phones stay paired across restarts; `--new-token` unpairs every device), POSTs need a CSRF token,
  uploads are capped (`--max-mb`, default 20), and there are no edit or delete routes. `--lan` is plain HTTP:
  use it on trusted Wi-Fi or Tailscale (`tailscale serve` for HTTPS).

Tests: `python3 -m unittest discover -s tests`. Install as a command: `pipx install .`
