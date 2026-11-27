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
relif init                                   # creates ~/.relif (or --home / $RELIF_HOME)
relif property add "Unit A" --rented-pct 60
relif add 42.10 "Hardware Co" --date 2026-03-04 --property 1 \
    --category "Repairs and maintenance (8960)" -r ~/scans/caulk.pdf
relif list --year 2026
relif void 2 --reason "typo, re-entered as #3"  # corrections never edit in place
relif verify                                  # re-walk the hash chain, re-hash receipts
relif export --year 2026                      # ~/.relif/exports/2026/ audit bundle
relif retention                               # what is past its retain-until date
relif rebuild                                 # restore derived tables from the event log
relif backup && relif restore-test # via restic, if configured
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
relif ask --kind repair --work restores               # cited verdict only
relif ask --kind utilities --use shared --property 1 \
    --amount 120 --vendor Hydro -r ~/scans/hydro.pdf               # verdict + ledger entry
relif serve                                            # phone capture page on localhost
relif serve --lan                                      # home Wi-Fi / Tailscale (opt-in)
```

- **Verdicts** (`backend/relif/verdict.py`): a pure decision tree over the 8 questions (use, availability, kind,
  interest vs principal, current vs capital, personal share, short-term rental compliance, profit). Outcomes:
  current (T776 line), capital (CCA class), partial (rental %), not deductible, unsure (ask an accountant).
  Every verdict cites CRA T4036 and lists its assumptions. Not tax advice.
- **Capture page**: one mobile form (amount, vendor, date, property, receipt photo, a few answers) that logs the
  entry and shows the verdict, plus a read-only recent-entries page with the chain status. Stdlib only.
  Binds `127.0.0.1` unless `--lan`; it prints a pairing link (cookie; the token is kept in `serve.token`,
  mode 0600, so phones stay paired across restarts; `--new-token` unpairs every device), POSTs need a CSRF token,
  uploads are capped (`--max-mb`, default 20), and there are no edit or delete routes. `--lan` is plain HTTP:
  use it on trusted Wi-Fi or Tailscale (`tailscale serve` for HTTPS).

## Layout, config and deploying

```
relif.yaml        one config: backend / frontend / deploy (`relif config` shows what is in effect)
backend/          the `relif` package (ledger, verdicts, CLI, capture server), Dockerfile, compose files
frontend/         app.py (the stlite page) + build.py (stdlib: writes frontend/site/)
tests/            python3 -m unittest discover -s tests -t .
vercel.json       Vercel builds frontend/ on every push
```

Install the CLI: `pipx install ./backend` (needs PyYAML, installed with it). Then, from the repo folder:

```bash
relif config                    # effective settings and where each comes from (flag > env > relif.yaml > default)
relif deploy backend            # per deploy.backend: docker | docker+tailscale | local (`relif serve`)
relif deploy frontend           # builds frontend/site/ locally; Vercel does the same on push
relif deploy backend --dry-run  # print the command instead
```

```yaml
backend:  { home: ~/.relif, bind: 127.0.0.1:8765, public_url: "", max_upload_mb: 20 }
frontend: { url: https://relif.vercel.app }        # its origin is the only one allowed to read /health
deploy:   { frontend: vercel, backend: docker }
```

The **backend** (ledger, receipts, capture server) always runs on your own machine. The **frontend** is a static
page with no ledger in it; it is the only part that is hosted.

## Run the backend at home (Docker)

The capture server needs a machine (Vercel only serves the static frontend), so it runs on your laptop in
Docker, restarts on its own and keeps the store in a normal host folder: `relif verify`, `export` and
`backup` keep working from the host CLI. `relif.yaml` is mounted read-only into the container.

```bash
cp backend/.env.example backend/.env       # set RELIF_UID/RELIF_GID to `id -u` / `id -g`
mkdir -p ~/.relif                          # create it yourself, or Docker makes it root-owned
docker compose -f backend/compose.yaml run --rm relif init   # once, if ~/.relif has no store yet
relif deploy backend                       # http://127.0.0.1:8765 on this laptop only
xdg-open "http://127.0.0.1:8765/pair?t=$(cat ~/.relif/serve.token)"   # pair this browser
```

**Phone, from anywhere (Tailscale, recommended).** Set `deploy.backend: docker+tailscale`. The sidecar makes the
server its own tailnet node `relif` with a real HTTPS certificate. Only devices on your tailnet can reach it
(`serve`, never `funnel`), and no host port is published. Put a tagged, non-reusable, pre-approved `TS_AUTHKEY`
and `RELIF_PUBLIC_URL=https://relif.<your-tailnet>.ts.net` in `backend/.env` (never commit it; this repo is
public), then `relif deploy backend` and read the pairing link and phone bookmark with
`docker compose -f backend/compose.yaml -f backend/compose.tailscale.yaml logs relif`.

With a public URL the pairing link is HTTPS, the cookie is `Secure`, and any other `Host` header gets `421`
(this also blocks DNS rebinding). `GET /health` is the one route that needs no pairing: it returns only
`{"ok": true, "version": ...}`, and only the `frontend.url` origin may read it.

**Open it from the frontend.** `relif serve` prints a bookmark like
`https://relif.vercel.app/#server=https://relif.<your-tailnet>.ts.net`. The address rides in the URL fragment,
which browsers never send to the host, so it is not in the public page or its logs. The page then shows
🟢 *Open my RELIF* or 😴 *laptop asleep or off your tailnet* within 3 seconds. Only `https://*.ts.net` addresses
are accepted, so a crafted link can't turn the page into a redirect.

**Keep the laptop awake.** `restart: unless-stopped` brings the server back after a reboot, but a sleeping
laptop can't answer. Disable suspend on AC power (or keep the lid open) when you want to log from your phone.

## The frontend: try the verdict in a browser

The "is this deductible?" questions also run as a static page: Streamlit in the browser via
[stlite](https://github.com/whitphx/stlite) (Python on WebAssembly), so it needs no server. Only the pure
decision tree ships (`backend/relif/verdict.py` + `backend/relif/demo.py` + `frontend/app.py`): no ledger, no
uploads, no browser storage; answers never leave the page. Real entries still go through `relif ask`/`relif add`
or the home capture page, so tax records stay on your machine.

```bash
python3 frontend/build.py                       # writes frontend/site/index.html (stlite pinned in build.py)
python3 -m http.server -d frontend/site 8000    # open http://localhost:8000 (first load fetches Python)
```

A golden test runs every row of the verdict table through both `relif ask` and the page adapter and
requires identical text.

**Deploy (Vercel).** Import this repo in Vercel once (framework "Other"; `vercel.json` sets the build command
and output folder). Every push to `main` then deploys production and every PR gets a preview URL. Put the
production URL in `relif.yaml` → `frontend.url` and restart the backend so `/health` and the bookmark match.
Tests run in GitHub Actions (`test.yml`); make that check required on `main` if a red test should block a
deploy. Public; not tax advice.
