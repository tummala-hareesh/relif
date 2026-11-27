import hashlib
import http.client
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from relif.store import Store
from relif.web import csrf_token, make_server, pairing_token

TOKEN = "pairing-token-for-tests"


def multipart(fields, files=()):
    boundary = "----relifTestBoundary7MA4YWxkTrZu0gW"
    out = b""
    for name, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
    for name, filename, data in files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-web-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        with Store.init(self.home) as s:
            s.add_property("Duplex", rented_pct=50)
        self.server = make_server(self.home, port=0, token=TOKEN, max_bytes=64 * 1024, quiet=True)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.cookie = f"relif_session={TOKEN}"

    def request(self, method, path, body=None, headers=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        h = dict(headers or {})
        if auth:
            h["Cookie"] = self.cookie
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        return resp, resp.read().decode("utf-8", "replace")

    def post_add(self, files=(), auth=True, csrf=None, **fields):
        data = {"csrf": csrf if csrf is not None else csrf_token(TOKEN), "amount": "42.10", "vendor": "Hardware Co",
                "date": "2026-03-04", "kind": "repair", "work": "restores", "use": "rental", **fields}
        body, ctype = multipart(data, files)
        return self.request("POST", "/add", body, {"Content-Type": ctype}, auth=auth)

    def entries(self):
        with Store(self.home) as s:
            return s.entries(), s

    def test_pairing_token_persists_until_rotated(self):
        first = pairing_token(self.home)
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(pairing_token(self.home), first)
        self.assertEqual((self.home / "serve.token").stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(pairing_token(self.home, rotate=True), first)
        with Store(self.home) as s:
            self.assertTrue(s.verify().ok)

    def test_truncated_body_logs_nothing(self):
        body, ctype = multipart({"csrf": csrf_token(TOKEN), "amount": "1", "vendor": "X"})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest("POST", "/add")
        conn.putheader("Cookie", self.cookie)
        conn.putheader("Content-Type", ctype)
        conn.putheader("Content-Length", str(len(body) + 100))
        conn.endheaders()
        conn.send(body)
        conn.sock.shutdown(1)  # half-close: server sees EOF before Content-Length bytes
        try:
            conn.getresponse().read()
        except (http.client.HTTPException, OSError):
            pass
        self.assertEqual(len(self.entries()[0]), 0)

    def test_default_bind_is_localhost(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_unauthenticated_requests_get_401(self):
        for path in ("/", "/entries"):
            resp, _ = self.request("GET", path, auth=False)
            self.assertEqual(resp.status, 401, path)
        resp, _ = self.post_add(auth=False)
        self.assertEqual(resp.status, 401)
        self.cookie = "relif_session=wrong"
        self.assertEqual(self.request("GET", "/")[0].status, 401)
        self.assertEqual(len(self.entries()[0]), 0)

    def test_pairing_sets_cookie(self):
        resp, _ = self.request("GET", "/pair?t=nope", auth=False)
        self.assertEqual(resp.status, 401)
        resp, _ = self.request("GET", f"/pair?t={TOKEN}", auth=False)
        self.assertEqual(resp.status, 303)
        cookie = resp.getheader("Set-Cookie")
        self.assertIn(f"relif_session={TOKEN}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_form_page(self):
        resp, body = self.request("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertIn('accept="image/*,application/pdf"', body)
        self.assertIn("capture", body)
        self.assertIn(csrf_token(TOKEN), body)
        self.assertIn("Duplex", body)
        self.assertIn("default-src 'none'", resp.getheader("Content-Security-Policy"))

    def test_add_with_receipt(self):
        photo = b"\xff\xd8\xff fake jpeg bytes \r\n--not-a-boundary\r\n"
        resp, body = self.post_add(files=[("receipt", "IMG_0001.jpg", photo)], description="caulk, Unit A",
                                   property="1")
        self.assertEqual(resp.status, 200, body)
        self.assertIn("Generally deductible: Repairs and maintenance (8960)", body)
        self.assertIn("entry #1", body)
        rows, _ = self.entries()
        self.assertEqual(len(rows), 1)
        e = rows[0]
        self.assertEqual((e["amount_cents"], e["vendor"], e["property_id"]), (4210, "Hardware Co", 1))
        with Store(self.home) as s:
            receipts = s.receipts_for(1)
            self.assertEqual(receipts[0]["sha256"], hashlib.sha256(photo).hexdigest())
            self.assertTrue(s.verify().ok)

    def test_shared_use_uses_property_pct(self):
        resp, body = self.post_add(kind="utilities", use="shared", property="1", work="")
        self.assertEqual(resp.status, 200, body)
        self.assertIn("Partly deductible: 50%", body)

    def test_add_without_receipt(self):
        resp, body = self.post_add()
        self.assertEqual(resp.status, 200, body)
        self.assertEqual(len(self.entries()[0]), 1)

    def test_bad_csrf_is_rejected(self):
        resp, _ = self.post_add(csrf="forged")
        self.assertEqual(resp.status, 403)
        resp, _ = self.post_add(csrf="")
        self.assertEqual(resp.status, 403)
        self.assertEqual(len(self.entries()[0]), 0)

    def test_bad_input_is_400_and_logs_nothing(self):
        for bad in (dict(amount="4.2.1"), dict(vendor=""), dict(kind="yacht"), dict(date="2026-13-40"),
                    dict(personal_pct="abc"), dict(property="99")):
            with self.subTest(bad=bad):
                resp, body = self.post_add(**bad)
                self.assertEqual(resp.status, 400, body)
        self.assertEqual(len(self.entries()[0]), 0)

    def test_html_is_escaped(self):
        resp, body = self.post_add(vendor="<script>alert(1)</script>")
        self.assertEqual(resp.status, 200)
        self.assertNotIn("<script>alert(1)</script>", body)
        resp, body = self.request("GET", "/entries")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_upload_size_limit(self):
        resp, _ = self.post_add(files=[("receipt", "big.jpg", b"x" * (65 * 1024))])
        self.assertEqual(resp.status, 413)
        self.assertEqual(len(self.entries()[0]), 0)

    def test_missing_length_or_wrong_type(self):
        resp, _ = self.request("POST", "/add", b"amount=1", {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(resp.status, 415)

    def test_no_route_modifies_or_deletes(self):
        self.post_add()
        for method in ("PUT", "DELETE", "PATCH"):
            resp, _ = self.request(method, "/entries/1")
            self.assertEqual(resp.status, 405, method)
        self.assertEqual(self.request("POST", "/void")[0].status, 404)
        self.assertEqual(self.request("GET", "/nope")[0].status, 404)
        rows, _ = self.entries()
        self.assertEqual(rows[0]["voided"], 0)

    def test_entries_page_shows_verify_status(self):
        self.post_add()
        resp, body = self.request("GET", "/entries")
        self.assertEqual(resp.status, 200)
        self.assertIn("chain intact", body)
        self.assertIn("Hardware Co", body)
        self.assertIn("42.10", body)


if __name__ == "__main__":
    unittest.main()
