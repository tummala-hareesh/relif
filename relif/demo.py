"""The "try the verdict" demo page: the pure decision tree as a static stlite (Streamlit-in-the-browser) page.

Only `verdict.py` and this adapter ship to the page. No ledger, no uploads, no browser storage: the answers
never leave the browser. `python -m relif.demo build --out site` writes the static site for GitHub Pages.
Stdlib only; this file also runs inside Pyodide.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from relif.verdict import Answers, decide, verdict_lines

STLITE_VERSION = "1.12.3"   # pinned @stlite/browser; bump deliberately and re-check on a phone
PAGE_FILES = {               # path inside the page -> path in the repo
    "app.py": "demo/app.py",
    "relif/__init__.py": "relif/__init__.py",
    "relif/verdict.py": "relif/verdict.py",
    "relif/demo.py": "relif/demo.py",
}
_INTS = {"rental_pct", "personal_pct"}
_FIELDS = {f.name for f in fields(Answers)}


def answers_from_form(form: dict) -> Answers:
    """Widget values (strings, '' for not given) -> Answers. Raises ValueError on anything unexpected."""
    unknown = set(form) - _FIELDS
    if unknown:
        raise ValueError(f"unknown answer(s): {', '.join(sorted(unknown))}")
    values = {}
    for key, value in form.items():
        if value == "" or value is None:
            continue
        if key in _INTS and isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f"{key} must be a whole number from 0 to 100 (got {value!r})") from None
        values[key] = value
    return Answers(**values)


def ask(form: dict) -> dict:
    """Same verdict and text as `relif ask` for the same answers; errors come back as an 'error' outcome."""
    try:
        v = decide(answers_from_form(form))
    except (ValueError, TypeError) as exc:
        return {"outcome": "error", "label": str(exc), "lines": [str(exc)]}
    return {"outcome": v.outcome, "label": v.label, "lines": verdict_lines(v)}


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
<title>RELIF: is this rental expense deductible?</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@stlite/browser@{version}/build/stlite.css">
</head>
<body>
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
    files = {dest: (Path(root) / src).read_text() for dest, src in PAGE_FILES.items()}
    data = json.dumps(files, sort_keys=True).replace("<", "\\u003c")   # can't close the <script> early
    return _PAGE.format(version=STLITE_VERSION, files=data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m relif.demo", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the static demo site (index.html) for GitHub Pages")
    b.add_argument("--out", default="site")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(build_html(Path(__file__).resolve().parent.parent))
    (out / ".nojekyll").write_text("")
    print(f"wrote {out / 'index.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
