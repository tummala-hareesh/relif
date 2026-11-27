"""`relif serve`: a thin, local phone capture page over Store + the verdict engine (stdlib only).

Security rules: binds 127.0.0.1 unless --lan/--host; every page needs the pairing cookie; POSTs need a CSRF token;
request bodies are size-capped; there are no edit or delete routes (corrections stay void + re-add in the CLI).
Behind HTTPS (`--public-url`, e.g. Tailscale serve) the cookie is `Secure` and any other Host header gets 421,
which also blocks DNS rebinding. `GET /health` is the one unauthenticated route: `{"ok", "version"}` only, no
ledger data, readable cross-origin by the Pages demo origin alone.
"""
from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
from datetime import date
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .capture import answers_for, log_expense
from .money import format_cents, parse_cents
from .store import RelifError, Store
from .verdict import KINDS, LOAN_PARTS, TRI, USES, WORK, decide

COOKIE = "relif_session"
TOKEN_FILE = "serve.token"
REQUEST_TIMEOUT = 30  # seconds per socket read: a stalled client can't hold a thread forever
DEFAULT_PORT = 8765
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
PAGES_ORIGIN = "https://tummala-hareesh.github.io"   # the public demo page; the only CORS origin, /health only
PAGES_DEMO = PAGES_ORIGIN + "/relif-demo/"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}   # container healthcheck / same machine
_SAFE_NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
STYLE = ("body{font:16px/1.4 system-ui,sans-serif;margin:0 auto;max-width:34rem;padding:1rem}"
         "label{display:block;margin:.6rem 0 .2rem;font-weight:600}input,select,textarea,button{width:100%;"
         "box-sizing:border-box;font-size:1rem;padding:.55rem}button{margin-top:1rem;font-weight:700}"
         "table{width:100%;border-collapse:collapse}td{padding:.3rem;border-bottom:1px solid #ddd}"
         ".v{padding:.8rem;border-left:4px solid #3a7;background:#f3faf5}.small{color:#555;font-size:.85rem}")


def csrf_token(token: str) -> str:
    return hmac.new(token.encode(), b"relif-csrf", sha256).hexdigest()


def validate_public_url(url: str) -> str:
    """The https origin the server is reached at, normalised (lower-case, no trailing slash). ValueError if not."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise ValueError(f"--public-url is not a URL: {url!r}") from None
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("--public-url must be an https:// address, e.g. https://relif.<tailnet>.ts.net")
    if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment \
            or "#" in url or "?" in url:
        raise ValueError("--public-url must be just scheme and host (no path, query, login or fragment)")
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def pages_bookmark(public_url: str) -> str:
    """The Pages demo link that knows this server. The address rides in the fragment: never sent to GitHub."""
    return f"{PAGES_DEMO}#server={public_url}"


def _host_name(host_header: str) -> str:
    """Host header without the port, lower-case ('' if missing or malformed)."""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[:end + 1] if end > 0 else ""
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _page(title: str, body: str) -> bytes:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{_e(title)}</title><style>{STYLE}</style>'
            f'</head><body>{body}<p class="small"><a href="/">Log a purchase</a> · <a href="/entries">Recent '
            f'entries</a></p><p class="small">Not tax advice. RELIF never files anything with the CRA.</p>'
            f'</body></html>').encode()


def _select(name: str, options, selected=None, blank=None) -> str:
    opts = f'<option value="">{_e(blank)}</option>' if blank is not None else ""
    for opt in options:
        sel = " selected" if opt == selected else ""
        opts += f'<option value="{_e(opt)}"{sel}>{_e(opt.replace("_", " "))}</option>'
    return f'<select name="{name}" id="{name}">{opts}</select>'


def parse_multipart(content_type: str, body: bytes) -> tuple[dict, dict]:
    """Returns (fields, files) where files maps name -> (filename, bytes). Stdlib `cgi` is gone in 3.13."""
    msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise ValueError("expected a multipart form")
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            if data:
                files[name] = (filename, data)
        else:
            fields[name] = data.decode("utf-8", "replace").strip()
    return fields, files


def _safe_filename(name: str) -> str:
    base = "".join(c if c in _SAFE_NAME else "_" for c in Path(name.replace("\\", "/")).name).lstrip(".")
    return base[:80] or "receipt"


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "relif"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT

    # ---- plumbing ------------------------------------------------------------------------
    def log_message(self, fmt, *args):  # never log the pairing token in the query string
        if not self.server.quiet and urlsplit(self.path).path != "/health":  # healthchecks every 30 s
            sys.stderr.write(f"{self.address_string()} {self.command} {urlsplit(self.path).path}\n")

    def send(self, status: int, body: bytes = b"", ctype="text/html; charset=utf-8", headers=None, close=False):
        self.send_response(status)
        for k, v in {**HEADERS, **(headers or {})}.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status: int, message: str, close=False):
        self.send(status, _page(message, f"<h1>{_e(status)}</h1><p>{_e(message)}</p>"), close=close)

    def host_ok(self) -> bool:
        if not self.server.allowed_hosts:
            return True
        return _host_name(self.headers.get("Host", "")) in self.server.allowed_hosts

    def authed(self) -> bool:
        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        got = jar[COOKIE].value if COOKIE in jar else ""
        return hmac.compare_digest(got.encode(), self.server.token.encode())

    def store(self) -> Store:
        return Store(self.server.home)

    # ---- routes --------------------------------------------------------------------------
    def do_GET(self):
        if not self.host_ok():
            return self.fail(421, "Wrong address for this RELIF server.", close=True)
        url = urlsplit(self.path)
        if url.path == "/health":
            body = json.dumps({"ok": True, "version": __version__}).encode()
            return self.send(200, body, "application/json",
                             headers={"Access-Control-Allow-Origin": PAGES_ORIGIN, "Vary": "Origin"})
        if url.path == "/pair":
            given = (parse_qs(url.query).get("t") or [""])[0]
            if not hmac.compare_digest(given.encode(), self.server.token.encode()):
                return self.fail(401, "Pairing link is not valid. Use the link printed by `relif serve`.")
            cookie = f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
            if self.server.public_url:
                cookie += "; Secure"
            return self.send(303, b"", headers={"Location": "/", "Set-Cookie": cookie})
        if not self.authed():
            return self.fail(401, "Not paired. Open the pairing link printed by `relif serve`.")
        try:
            if url.path == "/":
                return self.send(200, self.form_page())
            if url.path == "/entries":
                return self.send(200, self.entries_page())
        except (RelifError, sqlite3.Error, OSError) as exc:
            return self.fail(500, f"store error: {type(exc).__name__}")
        self.fail(404, "Not found")

    def do_POST(self):
        if not self.host_ok():
            return self.fail(421, "Wrong address for this RELIF server.", close=True)
        if not self.authed():
            return self.fail(401, "Not paired.", close=True)
        if urlsplit(self.path).path != "/add":
            return self.fail(404, "Not found", close=True)
        ctype = self.headers.get("Content-Type", "")
        if not ctype.lower().startswith("multipart/form-data"):
            return self.fail(415, "Send the form as multipart/form-data.", close=True)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.fail(411, "Content-Length required.", close=True)
        if length < 0:
            return self.fail(400, "Bad Content-Length.", close=True)
        if length > self.server.max_bytes:
            if length <= 10 * self.server.max_bytes:  # drain so the client sees the 413, not a reset
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            mb = self.server.max_bytes / (1024 * 1024)
            return self.fail(413, f"Upload too large (limit {mb:g} MB).", close=True)
        try:
            body = self.rfile.read(length)
        except (TimeoutError, OSError):
            self.close_connection = True
            return
        if len(body) < length:
            self.close_connection = True
            return
        try:
            fields, files = parse_multipart(ctype, body)
        except (ValueError, IndexError, TypeError):
            return self.fail(400, "Could not read the form.")
        if not hmac.compare_digest(fields.get("csrf", "").encode(), csrf_token(self.server.token).encode()):
            return self.fail(403, "Form expired. Reload the page and try again.")
        try:
            self.send(200, self.add(fields, files))
        except (ValueError, RelifError) as exc:
            self.fail(400, str(exc))
        except (sqlite3.Error, OSError) as exc:
            self.fail(500, f"store error: {type(exc).__name__}")

    def do_OPTIONS(self):  # CORS / Local Network Access preflight, for /health only
        if not self.host_ok():
            return self.fail(421, "Wrong address for this RELIF server.", close=True)
        if urlsplit(self.path).path != "/health":
            return self.fail(405, "Not allowed.", close=True)
        self.send(204, headers={"Access-Control-Allow-Origin": PAGES_ORIGIN, "Access-Control-Allow-Methods": "GET",
                                "Access-Control-Allow-Private-Network": "true", "Access-Control-Max-Age": "600",
                                "Vary": "Origin"})

    def do_PUT(self):
        self.fail(405, "Entries cannot be changed. Void and re-add with the CLI.", close=True)

    do_DELETE = do_PATCH = do_PUT

    # ---- pages ---------------------------------------------------------------------------
    def form_page(self) -> bytes:
        with self.store() as s:
            props = s.properties()
        popts = '<option value="">(none)</option>' + "".join(
            f'<option value="{p["id"]}">{_e(p["name"])} (rented {p["rented_pct"]}%)</option>'
            for p in props if not p["disposed_on"])
        body = f"""<h1>Log a purchase</h1>
<form method="post" action="/add" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{csrf_token(self.server.token)}">
<label for="amount">Amount ($)</label><input id="amount" name="amount" inputmode="decimal" required>
<label for="vendor">Vendor</label><input id="vendor" name="vendor" required>
<label for="date">Date</label><input id="date" name="date" type="date" value="{date.today().isoformat()}">
<label for="property">Property</label><select id="property" name="property">{popts}</select>
<label for="description">What was it?</label><input id="description" name="description">
<label for="receipt">Receipt photo</label>
<input id="receipt" name="receipt" type="file" accept="image/*,application/pdf" capture="environment">
<label for="kind">Kind of purchase</label>{_select("kind", KINDS, blank="(choose)")}
<label for="use">Used for</label>{_select("use", USES, "rental")}
<label for="work">For repairs: the work…</label>{_select("work", WORK, blank="(not a repair)")}
<label for="loan_part">For loan payments</label>{_select("loan_part", LOAN_PARTS, blank="(not a loan)")}
<label for="personal_pct">Personal share (%)</label>
<input id="personal_pct" name="personal_pct" inputmode="numeric" value="0">
<label for="available">Available for rent then?</label>{_select("available", TRI, "yes")}
<label for="profit">Expect a profit (market rent)?</label>{_select("profit", TRI, "yes")}
<label><input type="checkbox" name="short_term" style="width:auto"> Short-term rental (under 90 days)</label>
<label for="str_compliant">Short-term rental licensed?</label>{_select("str_compliant", TRI, blank="(n/a)")}
<button type="submit">Ask and log</button>
</form>"""
        return _page("RELIF: log a purchase", body)

    def add(self, fields: dict, files: dict) -> bytes:
        def opt(name):
            return fields.get(name) or None

        def pct(name, default):
            raw = fields.get(name, "")
            if raw == "":
                return default
            if not raw.isdigit():
                raise ValueError(f"{name} must be a whole number from 0 to 100")
            return int(raw)

        cents = parse_cents(fields.get("amount", ""))
        vendor = fields.get("vendor", "")
        if not vendor:
            raise ValueError("vendor is required")
        prop = opt("property")
        if prop is not None and not prop.isdigit():
            raise ValueError("property must be a number")
        property_id = int(prop) if prop else None
        tmpdir = None
        with self.store() as s:
            v = decide(answers_for(s, property_id, rental_pct=pct("rental_pct", None), kind=opt("kind"),
                                   use=fields.get("use") or "rental", available=fields.get("available") or "yes",
                                   loan_part=opt("loan_part"), work=opt("work"),
                                   personal_pct=pct("personal_pct", 0), short_term=bool(fields.get("short_term")),
                                   str_compliant=opt("str_compliant"), profit=fields.get("profit") or "yes"))
            receipts = []
            try:
                if "receipt" in files:
                    tmpdir = Path(tempfile.mkdtemp(prefix="relif-upload-"))
                    filename, data = files["receipt"]
                    path = tmpdir / _safe_filename(filename)
                    path.write_bytes(data)
                    receipts.append(path)
                eid = log_expense(s, v, amount_cents=cents, date=fields.get("date") or date.today().isoformat(),
                                  vendor=vendor, description=opt("description"), property_id=property_id,
                                  receipts=receipts)
            finally:
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            stored = s.receipts_for(eid)
        notes = "".join(f"<li>{_e(n)}</li>" for n in v.assumptions)
        rec = "".join(f"<p class='small'>receipt sha256 {_e(r['sha256'])}</p>" for r in stored) or \
            "<p class='small'>No receipt attached: keep the paper original.</p>"
        body = (f"<h1>Logged entry #{eid}</h1><div class='v'><strong>{_e(v.label)}</strong><p>{_e(v.reason)}</p>"
                f"<ul>{notes}</ul><p class='small'>{_e(v.citation)}</p></div>"
                f"<p>entry #{eid} · {_e(format_cents(cents))} · {_e(vendor)}</p>{rec}"
                f"<p class='small'>{_e(v.disclaimer)}</p>")
        return _page(f"RELIF: entry #{eid}", body)

    def entries_page(self) -> bytes:
        with self.store() as s:
            rows = s.entries()[-20:][::-1]
            report = s.verify()
        if report.ok:
            status = f"✅ chain intact, {report.events} events, {report.receipts} receipt files"
        else:
            status = f"❌ {len(report.problems)} problem(s): run <code>relif verify</code>"
        if report.last_anchor:
            status += f", last anchored {_e(report.last_anchor['ts'][:10])}"
        trs = "".join(
            f"<tr><td>#{e['id']}</td><td>{_e(e['date'])}</td><td>{_e(format_cents(e['amount_cents']))}</td>"
            f"<td>{_e(e['vendor'])}{' (void)' if e['voided'] else ''}<br><span class='small'>"
            f"{_e(e['verdict'] or e['category'] or '')}</span></td></tr>" for e in rows)
        return _page("RELIF: recent entries", f"<h1>Recent entries</h1><p>{status}</p><table>{trs}</table>")


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, home, address, token: str, max_bytes: int, quiet: bool = False, public_url: str = None):
        self.home = Path(home)
        self.public_url = validate_public_url(public_url) if public_url else None
        self.allowed_hosts = ({_host_name(urlsplit(self.public_url).netloc)} | _LOCAL_HOSTS) if self.public_url else set()
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.quiet = quiet
        self.token = token
        self.max_bytes = max_bytes
        super().__init__(address, CaptureHandler)


def pairing_token(home, *, rotate: bool = False) -> str:
    """The pairing token, kept in the store folder (mode 0600) so paired phones survive a restart."""
    path = Path(home).expanduser() / TOKEN_FILE
    if not rotate:
        try:
            token = path.read_text(encoding="ascii").strip()
            if len(token) >= 32:
                return token
        except (OSError, UnicodeDecodeError):
            pass
    token = secrets.token_urlsafe(32)
    fd = os.open(path.with_name(TOKEN_FILE + ".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(token + "\n")
    os.replace(path.with_name(TOKEN_FILE + ".tmp"), path)
    return token


def make_server(home, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT, token: str = None,
                max_bytes: int = DEFAULT_MAX_BYTES, quiet: bool = False, public_url: str = None) -> CaptureServer:
    Store(home).close()  # fail fast if there is no store
    return CaptureServer(home, (host, port), token or pairing_token(home), max_bytes, quiet, public_url)


def lan_address() -> str:
    """Best guess at this machine's LAN address (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
