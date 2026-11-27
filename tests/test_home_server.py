"""Issue #10: `relif serve` in Docker behind Tailscale, linked from the Pages demo (--host, --public-url, /health)."""
import http.client
import io
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from relif import __version__
from relif.cli import build_parser, main
from relif.store import Store
from relif.web import make_server, pages_bookmark, validate_public_url

FRONTEND = "https://relif.vercel.app"   # default frontend.url in relif.yaml

TOKEN = "pairing-token-for-tests"
PUBLIC = "https://relif.example-tailnet.ts.net"


class ServerCase(unittest.TestCase):
    public_url = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-home-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        with Store.init(self.home) as s:
            s.add_property("Duplex", rented_pct=50)
        self.server = make_server(self.home, port=0, token=TOKEN, quiet=True, public_url=self.public_url)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, method, path, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest(method, path, skip_host=host is not None)
        if host is not None:
            conn.putheader("Host", host)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        conn.endheaders()
        resp = conn.getresponse()
        return resp, resp.read().decode("utf-8", "replace")


class HealthTest(ServerCase):
    def test_health_needs_no_pairing_and_shows_no_ledger_data(self):
        resp, body = self.request("GET", "/health")
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.getheader("Content-Type").startswith("application/json"))
        self.assertEqual(json.loads(body), {"ok": True, "version": __version__})
        self.assertNotIn("Duplex", body)

    def test_health_allows_only_the_frontend_origin(self):
        resp, _ = self.request("GET", "/health", {"Origin": "https://evil.example"})
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), FRONTEND)
        self.assertIsNone(resp.getheader("Access-Control-Allow-Credentials"))

    def test_cors_header_is_only_on_health(self):
        for path in ("/", "/entries", "/pair?t=nope", "/nope"):
            resp, _ = self.request("GET", path)
            self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"), path)

    def test_preflight_only_for_health(self):
        resp, _ = self.request("OPTIONS", "/health", {"Origin": FRONTEND,
                                                      "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(resp.status, 204)
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), FRONTEND)
        self.assertEqual(resp.getheader("Access-Control-Allow-Private-Network"), "true")
        resp, _ = self.request("OPTIONS", "/add")
        self.assertEqual(resp.status, 405)
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))

    def test_health_is_get_only(self):
        resp, _ = self.request("POST", "/health", {"Content-Length": "0"})
        self.assertEqual(resp.status, 401)

    def test_cookie_is_not_secure_without_public_url(self):
        resp, _ = self.request("GET", f"/pair?t={TOKEN}")
        self.assertNotIn("Secure", resp.getheader("Set-Cookie"))

    def test_any_host_is_accepted_without_public_url(self):
        resp, _ = self.request("GET", "/health", host="192.168.1.20:8765")
        self.assertEqual(resp.status, 200)


class PublicUrlTest(ServerCase):
    public_url = PUBLIC

    def test_cookie_is_secure_behind_https(self):
        resp, _ = self.request("GET", f"/pair?t={TOKEN}", host="relif.example-tailnet.ts.net")
        self.assertEqual(resp.status, 303)
        cookie = resp.getheader("Set-Cookie")
        self.assertIn("; Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_public_host_and_localhost_are_allowed(self):
        for host in ("relif.example-tailnet.ts.net", "RELIF.example-tailnet.ts.net:443", "127.0.0.1:8765",
                     "localhost", "[::1]:8765"):
            resp, _ = self.request("GET", "/health", host=host)
            self.assertEqual(resp.status, 200, host)

    def test_other_hosts_get_421(self):  # also blocks DNS rebinding
        for method, host in (("GET", "attacker.example"), ("GET", "relif.example-tailnet.ts.net.evil.example"),
                             ("POST", "attacker.example"), ("GET", "")):
            resp, _ = self.request(method, "/", {"Content-Length": "0"} if method == "POST" else None, host=host)
            self.assertEqual(resp.status, 421, (method, host))
            self.assertIsNone(resp.getheader("Set-Cookie"))

    def test_missing_host_gets_421(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest("GET", f"/pair?t={TOKEN}", skip_host=True)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 421)


class Ipv6Test(unittest.TestCase):
    def test_ipv6_bind_and_public_url(self):
        tmp = Path(tempfile.mkdtemp(prefix="relif-v6-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        Store.init(tmp / "home").close()
        try:
            server = make_server(tmp / "home", host="::1", port=0, token=TOKEN, quiet=True,
                                 public_url="https://[fd7a::1]")
        except OSError as exc:
            self.skipTest(f"no IPv6 loopback: {exc}")
        self.addCleanup(server.server_close)
        self.assertIn("[fd7a::1]", server.allowed_hosts)


class ValidateTest(unittest.TestCase):
    def test_accepts_https_origin(self):
        self.assertEqual(validate_public_url("https://relif.tail1234.ts.net/"), "https://relif.tail1234.ts.net")
        self.assertEqual(validate_public_url("https://Home.Example:8443"), "https://home.example:8443")
        self.assertEqual(validate_public_url("https://[FD7A::1]"), "https://[fd7a::1]")

    def test_rejects_anything_else(self):
        for bad in ("http://relif.ts.net", "relif.ts.net", "https://", "https://a.ts.net/path",
                    "https://u:p@a.ts.net", "https://a.ts.net/?q=1", "https://a.ts.net/#x", "ftp://a.ts.net"):
            with self.assertRaises(ValueError, msg=bad):
                validate_public_url(bad)

    def test_bookmark_puts_the_server_in_the_fragment(self):
        self.assertEqual(pages_bookmark(PUBLIC), FRONTEND + "/#server=https://relif.example-tailnet.ts.net")
        self.assertEqual(pages_bookmark(PUBLIC, "https://relif.example.org/app"),
                         "https://relif.example.org/app/#server=" + PUBLIC)


class ConfiguredOriginTest(ServerCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-home-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        Store.init(self.home).close()
        self.server = make_server(self.home, port=0, token=TOKEN, quiet=True,
                                  frontend_url="https://relif.example.org/app/")
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def test_cors_origin_comes_from_frontend_url(self):
        resp, _ = self.request("GET", "/health")
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), "https://relif.example.org")
        resp, _ = self.request("OPTIONS", "/health")
        self.assertEqual(resp.getheader("Access-Control-Allow-Origin"), "https://relif.example.org")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = str(self.tmp / "home")
        self.assertEqual(main(["--home", self.home, "init"], out=io.StringIO()), 0)

    def serve(self, *flags, env=None):
        out = io.StringIO()
        captured = {}

        class FakeServer:
            server_address = ("0.0.0.0", 8765)
            token = "t" * 43

            def serve_forever(self):
                pass

            def server_close(self):
                pass

        def fake_make_server(home, **kw):
            captured.update(kw)
            return FakeServer()

        with mock.patch.dict("os.environ", env or {}, clear=False), \
                mock.patch("relif.web.make_server", fake_make_server), \
                mock.patch("relif.web.lan_address", lambda: "192.168.1.5"):
            with mock.patch("sys.stderr", io.StringIO()):
                code = main(["--home", self.home, "serve", *flags], out=out)
        return code, out.getvalue(), captured

    def test_default_is_localhost(self):
        code, _, kw = self.serve()
        self.assertEqual((code, kw["host"], kw["public_url"]), (0, "127.0.0.1", None))

    def test_host_flag_sets_the_bind_address(self):
        code, out, kw = self.serve("--host", "0.0.0.0")
        self.assertEqual((code, kw["host"]), (0, "0.0.0.0"))
        self.assertIn("http://192.168.1.5:8765/pair?t=", out)
        self.assertIn("http://127.0.0.1:8765/pair?t=", out)   # what Docker actually publishes

    def test_lan_still_works(self):
        self.assertEqual(self.serve("--lan")[2]["host"], "0.0.0.0")

    def test_public_url_prints_https_pairing_link_and_bookmark(self):
        code, out, kw = self.serve("--host", "0.0.0.0", "--public-url", PUBLIC + "/")
        self.assertEqual((code, kw["public_url"]), (0, PUBLIC))
        self.assertIn(f"{PUBLIC}/pair?t=", out)
        self.assertNotIn("http://", out)
        self.assertIn(FRONTEND + "/#server=" + PUBLIC, out)

    def test_public_url_from_environment(self):
        self.assertEqual(self.serve(env={"RELIF_PUBLIC_URL": PUBLIC})[2]["public_url"], PUBLIC)
        self.assertIsNone(self.serve(env={"RELIF_PUBLIC_URL": ""})[2]["public_url"])

    def test_serve_takes_bind_upload_cap_and_frontend_from_relif_yaml(self):
        cfg = self.tmp / "relif.yaml"
        cfg.write_text("backend:\n  bind: 0.0.0.0:9001\n  max_upload_mb: 3\n"
                       "frontend:\n  url: https://relif.example.org/\n")
        out = io.StringIO()
        with mock.patch("relif.web.make_server") as ms, mock.patch("relif.web.lan_address", lambda: "10.0.0.2"), \
                mock.patch("sys.stderr", io.StringIO()):
            ms.return_value.server_address = ("0.0.0.0", 9001)
            ms.return_value.token = "t" * 43
            code = main(["--config", str(cfg), "--home", self.home, "serve"], out=out)
        self.assertEqual(code, 0)
        kw = ms.call_args.kwargs
        self.assertEqual((kw["host"], kw["port"], kw["max_bytes"], kw["frontend_url"]),
                         ("0.0.0.0", 9001, 3 * 1024 * 1024, "https://relif.example.org/"))

    def test_flags_beat_relif_yaml(self):
        cfg = self.tmp / "relif.yaml"
        cfg.write_text("backend:\n  bind: 0.0.0.0:9001\n  max_upload_mb: 3\n")
        with mock.patch("relif.web.make_server") as ms, mock.patch("sys.stderr", io.StringIO()):
            ms.return_value.server_address = ("127.0.0.1", 8000)
            ms.return_value.token = "t" * 43
            main(["--config", str(cfg), "--home", self.home, "serve", "--host", "127.0.0.1", "--port", "8000",
                  "--max-mb", "7"], out=io.StringIO())
        kw = ms.call_args.kwargs
        self.assertEqual((kw["host"], kw["port"], kw["max_bytes"]), ("127.0.0.1", 8000, 7 * 1024 * 1024))

    def test_bad_public_url_is_an_error(self):
        code, _, kw = self.serve("--public-url", "http://relif.ts.net")
        self.assertNotEqual(code, 0)
        self.assertEqual(kw, {})

    def test_help_mentions_new_flags(self):
        text = build_parser()._subparsers._group_actions[0].choices["serve"].format_help()
        self.assertIn("--host", text)
        self.assertIn("--public-url", text)


ROOT = Path(__file__).resolve().parent.parent


class DockerFilesTest(unittest.TestCase):
    def test_image_runs_as_non_root_on_data(self):
        text = (ROOT / "backend/Dockerfile").read_text()
        self.assertIn("USER relif", text)
        self.assertIn("RELIF_HOME=/data", text)
        self.assertIn("/health", text)
        self.assertNotIn("requirements", text)  # stdlib only

    def test_compose_publishes_on_localhost_only(self):
        text = (ROOT / "backend/compose.yaml").read_text()
        self.assertIn('"127.0.0.1:${RELIF_PORT:-8765}:8765"', text)
        self.assertIn("restart: unless-stopped", text)
        self.assertIn(":/data", text)

    def test_tailscale_sidecar_is_tailnet_only(self):
        compose = (ROOT / "backend/compose.tailscale.yaml").read_text()
        self.assertIn("ports: !reset []", compose)
        self.assertIn("network_mode: service:tailscale", compose)
        self.assertNotIn("funnel", compose.lower().replace("not `funnel`", ""))
        serve = json.loads((ROOT / "backend/tailscale/serve.json").read_text())
        self.assertEqual(serve["AllowFunnel"], {"${TS_CERT_DOMAIN}:443": False})

    def test_secrets_stay_out_of_git(self):
        ignored = (ROOT / ".gitignore").read_text().split()
        self.assertIn(".env", ignored)
        self.assertRegex((ROOT / "backend/.env.example").read_text(), r"(?m)^TS_AUTHKEY=$")


if __name__ == "__main__":
    unittest.main()
