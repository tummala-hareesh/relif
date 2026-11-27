#!/usr/bin/env python3
"""Build the RELIF frontend: the "is it deductible?" page as one static stlite (Streamlit-in-the-browser) file.

Only `backend/relif/verdict.py`, the answer adapter `backend/relif/demo.py` and `frontend/app.py` ship to the page.
No ledger, no uploads, no browser storage: the answers never leave the browser. Owners who run the backend at home
get an "open my RELIF" link: their bookmark carries the server address in the URL fragment
(`#server=https://relif.<tailnet>.ts.net`), so it is never sent to the host or baked into the build.

    python3 frontend/build.py --out frontend/site      # what vercel.json runs

Stdlib only, so Vercel's build image needs no `pip install`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STLITE_VERSION = "1.12.3"   # pinned @stlite/browser; bump deliberately and re-check on a phone
PAGE_FILES = {               # path inside the page -> path in the repo
    "app.py": "frontend/app.py",
    "relif/__init__.py": "backend/relif/__init__.py",
    "relif/verdict.py": "backend/relif/verdict.py",
    "relif/demo.py": "backend/relif/demo.py",
}

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
<title>RELIF: is this rental expense deductible?</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@stlite/browser@{version}/build/stlite.css">
</head>
<body>
<div id="relif-home" hidden style="font-family:sans-serif;padding:.6em 1em;background:#f3faf5;border-bottom:1px solid #cde"></div>
<script>
// relif-home: your own RELIF at home, from the #server=... fragment of your bookmark (an https *.ts.net address).
function relifServer(hash) {{
  const m = /^#(?:.*&)?server=([^&]*)/.exec(hash || "");
  if (!m) return null;
  let u;
  try {{ u = new URL(decodeURIComponent(m[1])); }} catch (e) {{ return null; }}
  if (u.protocol !== "https:" || u.username || u.password || !/^[a-z0-9-]+(\\.[a-z0-9-]+)*\\.ts\\.net$/.test(u.hostname)) return null;
  if ((u.pathname !== "/" && u.pathname !== "") || u.search || u.hash) return null;
  return u.origin;
}}
(function () {{
  const origin = relifServer(location.hash);
  if (!origin) return;
  const box = document.getElementById("relif-home");
  const say = (text, link) => {{
    box.textContent = text;
    if (link) {{ const a = document.createElement("a"); a.href = origin + "/"; a.rel = "noreferrer";
                 a.textContent = link; box.append(" ", a); }}
    box.hidden = false;
  }};
  say("Checking " + new URL(origin).hostname + "...");
  const stop = new AbortController();
  setTimeout(() => stop.abort(), 3000);
  fetch(origin + "/health", {{ mode: "cors", credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer",
                              signal: stop.signal }})
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((j) => {{ if (j.ok !== true) throw new Error("bad health");
                   say("🟢 " + new URL(origin).hostname + " is online. Only open it if that is your own laptop.", "Open my RELIF"); }})
    .catch(() => say("😴 Your RELIF is not answering: the laptop is asleep or this phone is off your tailnet."));
}})();
</script>
<noscript>This page runs Python in your browser and needs JavaScript.</noscript>
<div id="root"><p style="font-family:sans-serif;padding:1em">Loading Python in your browser (first visit can
take a few seconds)... Not tax advice. Nothing you enter leaves this page.</p></div>
<script type="module">
import {{ mount }} from "https://cdn.jsdelivr.net/npm/@stlite/browser@{version}/build/stlite.js";
const files = JSON.parse(document.getElementById("relif-files").textContent);
mount({{ entrypoint: "app.py", requirements: [], files }}, document.getElementById("root"));
</script>
<script type="application/json" id="relif-files">{files}</script>
</body>
</html>
"""


def build_html(root: Path) -> str:
    files = {dest: (Path(root) / src).read_text(encoding="utf-8") for dest, src in PAGE_FILES.items()}
    data = json.dumps(files, sort_keys=True).replace("<", "\\u003c")   # can't close the <script> early
    return _PAGE.format(version=STLITE_VERSION, files=data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="frontend/build.py", description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "frontend" / "site"))
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(build_html(ROOT), encoding="utf-8")
    (out / ".nojekyll").write_text("")
    print(f"wrote {out / 'index.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
