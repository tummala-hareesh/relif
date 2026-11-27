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

## Run it at home (Docker)

The capture server needs a machine (GitHub Pages only serves static files), so it runs on your laptop in
Docker, restarts on its own and keeps the store in a normal host folder: `relif verify`, `export` and
`backup` keep working from the host CLI.

```bash
cp .env.example .env                       # set RELIF_UID/RELIF_GID to `id -u` / `id -g`
mkdir -p ~/.relif                          # create it yourself, or Docker makes it root-owned
docker compose run --rm relif init         # once, if ~/.relif has no store yet
docker compose up -d --build               # http://127.0.0.1:8765 on this laptop only
xdg-open "http://127.0.0.1:8765/pair?t=$(cat ~/.relif/serve.token)"   # pair this browser
```

**Phone, from anywhere (Tailscale, recommended).** The sidecar makes the server its own tailnet node `relif`
with a real HTTPS certificate. Only devices on your tailnet can reach it (`serve`, never `funnel`), and no host
port is published. Put a tagged, non-reusable, pre-approved `TS_AUTHKEY` and
`RELIF_PUBLIC_URL=https://relif.<your-tailnet>.ts.net` in `.env` (never commit it), then:

```bash
docker compose -f compose.yaml -f compose.tailscale.yaml up -d --build
docker compose -f compose.yaml -f compose.tailscale.yaml logs relif   # pairing link + phone bookmark
```

With `--public-url` (here `RELIF_PUBLIC_URL`) the pairing link is HTTPS, the cookie is `Secure`, and any
other `Host` header gets `421` (this also blocks DNS rebinding). `GET /health` is the one route that needs
no pairing: it returns only `{"ok": true, "version": ...}`, and only the Pages demo origin may read it.

**Open it from the demo page.** `relif serve` prints a bookmark like
`https://tummala-hareesh.github.io/relif-demo/#server=https://relif.<your-tailnet>.ts.net`. The address
rides in the URL fragment, which browsers never send to GitHub, so it is not in the public page or its logs.
The page then shows 🟢 *Open my RELIF* or 😴 *laptop asleep or off your tailnet* within 3 seconds. Only
`https://*.ts.net` addresses are accepted, so a crafted link can't turn the page into a redirect.

**Keep the laptop awake.** `restart: unless-stopped` brings the server back after a reboot, but a sleeping
laptop can't answer. Disable suspend on AC power (or keep the lid open) when you want to log from your phone.

## Try the verdict in a browser (demo page)

The "is this deductible?" questions also run as a static page: Streamlit in the browser via
[stlite](https://github.com/whitphx/stlite) (Python on WebAssembly), so it can be served from GitHub Pages
with no server. Only the pure decision tree ships (`relif/verdict.py` + `relif/demo.py` + `demo/app.py`):
no ledger, no uploads, no browser storage; answers never leave the page. Real entries still go through
`relif ask`/`relif add` or the local `relif serve` capture page, so tax records stay on your machine.

```bash
python3 -m relif.demo build --out site        # writes site/index.html (stlite pinned in relif/demo.py)
python3 -m http.server -d site 8000           # open http://localhost:8000 (first load fetches Python, ~tens of MB)
```

A golden test runs every row of the verdict table through both `relif ask` and the page adapter and
requires identical text.

Demo URL (live after the first `demo-v*` deploy): <https://tummala-hareesh.github.io/relif-demo/>. Public; not tax advice.

Deploy: pushing a `demo-v*` tag runs `.github/workflows/demo-pages.yml`, which runs the tests, builds the site
and pushes only `site/` (no history) to the `gh-pages` branch of the public `tummala-hareesh/relif-demo` repo.
This repo stays private. One-time setup: create `relif-demo`, add an ed25519 deploy key with write access there,
store its private key as the Actions secret `DEMO_DEPLOY_KEY` in this repo, and enable Pages on `relif-demo`
(deploy from branch `gh-pages`, root). A failing test blocks the deploy.
