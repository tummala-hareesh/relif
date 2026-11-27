"""relif.yaml: one simple config for backend / frontend / deploy (issue #12)."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from relif import config
from relif.cli import main
from relif.store import RelifError

ROOT = Path(__file__).resolve().parent.parent


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="relif-cfg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("RELIF_CONFIG", "RELIF_HOME", "RELIF_PUBLIC_URL"):
            os.environ.pop(k, None)

    def write(self, text, name="relif.yaml"):
        p = self.tmp / name
        p.write_text(text)
        return p


class TestLoad(Tmp):
    def test_no_file_gives_defaults(self):
        c = config.load()
        self.assertIsNone(c.path)
        self.assertEqual((c.host, c.port, c.max_upload_mb, c.public_url), ("127.0.0.1", 8765, 20, None))
        self.assertEqual(c.home, Path("~/.relif").expanduser())
        self.assertEqual((c.deploy_frontend, c.deploy_backend), ("vercel", "docker"))
        self.assertEqual(c.frontend_origin, "https://relif.vercel.app")
        self.assertEqual(c.source["backend.bind"], "default")

    def test_file_in_cwd_is_found_and_read(self):
        self.write("backend:\n  home: /srv/relif\n  bind: 0.0.0.0:9000\n  max_upload_mb: 5\n"
                   "frontend:\n  url: https://relif.example.org/app/\n"
                   "deploy:\n  frontend: none\n  backend: docker+tailscale\n")
        c = config.load()
        self.assertEqual(c.path, self.tmp / "relif.yaml")
        self.assertEqual((c.home, c.host, c.port, c.max_upload_mb), (Path("/srv/relif"), "0.0.0.0", 9000, 5))
        self.assertEqual(c.frontend_origin, "https://relif.example.org")
        self.assertEqual(c.frontend_url, "https://relif.example.org/app/")
        self.assertEqual((c.deploy_frontend, c.deploy_backend), ("none", "docker+tailscale"))
        self.assertEqual(c.source["backend.bind"], "file")
        self.assertEqual(c.source["frontend.url"], "file")

    def test_explicit_path_beats_env_beats_cwd(self):
        self.write("backend:\n  max_upload_mb: 1\n")
        env_file = self.write("backend:\n  max_upload_mb: 2\n", "env.yaml")
        flag_file = self.write("backend:\n  max_upload_mb: 3\n", "flag.yaml")
        self.assertEqual(config.load().max_upload_mb, 1)
        os.environ["RELIF_CONFIG"] = str(env_file)
        self.assertEqual(config.load().max_upload_mb, 2)
        self.assertEqual(config.load(flag_file).max_upload_mb, 3)

    def test_named_file_must_exist(self):
        with self.assertRaisesRegex(RelifError, "not found"):
            config.load(self.tmp / "missing.yaml")

    def test_env_beats_file(self):
        self.write("backend:\n  home: /from/file\n  public_url: https://file.example.net\n")
        os.environ["RELIF_HOME"] = "/from/env"
        os.environ["RELIF_PUBLIC_URL"] = "https://relif.tail1.ts.net"
        c = config.load()
        self.assertEqual((c.home, c.public_url), (Path("/from/env"), "https://relif.tail1.ts.net"))
        self.assertEqual((c.source["backend.home"], c.source["backend.public_url"]), ("env", "env"))

    def test_empty_env_does_not_hide_the_file(self):
        self.write("backend:\n  public_url: https://relif.example.net\n")
        os.environ["RELIF_PUBLIC_URL"] = ""
        self.assertEqual(config.load().public_url, "https://relif.example.net")

    def test_ipv6_bind(self):
        self.write("backend:\n  bind: '[::]:8765'\n")
        self.assertEqual((config.load().host, config.load().port), ("::", 8765))

    def test_empty_file_and_empty_sections_are_defaults(self):
        self.write("")
        self.assertEqual(config.load().port, 8765)
        self.write("backend:\nfrontend:\n")
        self.assertEqual(config.load().port, 8765)


class TestValidation(Tmp):
    def bad(self, text, pattern):
        self.write(text)
        with self.assertRaisesRegex(RelifError, pattern):
            config.load()

    def test_unknown_section_and_key_are_named(self):
        self.bad("backnd:\n  home: x\n", "backnd")
        self.bad("backend:\n  prot: 1\n", "backend.prot")

    def test_bad_values_are_named(self):
        self.bad("backend:\n  bind: localhost\n", "backend.bind")
        self.bad("backend:\n  bind: 127.0.0.1:99999\n", "backend.bind")
        self.bad("backend:\n  max_upload_mb: 0\n", "backend.max_upload_mb")
        self.bad("backend:\n  max_upload_mb: lots\n", "backend.max_upload_mb")
        self.bad("backend:\n  public_url: http://relif.example\n", "backend.public_url")
        self.bad("frontend:\n  url: http://relif.vercel.app\n", "frontend.url")
        self.bad("frontend:\n  url: https://u:p@relif.vercel.app\n", "frontend.url")
        self.bad("deploy:\n  frontend: netlify\n", "deploy.frontend")
        self.bad("deploy:\n  backend: k8s\n", "deploy.backend")

    def test_not_a_mapping_or_not_yaml(self):
        self.bad("- a\n- b\n", "mapping")
        self.bad("backend: [1, 2]\n", "backend")
        self.bad("backend:\n  home: [unclosed\n", "YAML")


class TestRepoFile(unittest.TestCase):
    def test_committed_relif_yaml_is_valid_and_documents_every_key(self):
        c = config.load(ROOT / "relif.yaml")
        self.assertEqual(c.deploy_frontend, "vercel")
        text = (ROOT / "relif.yaml").read_text()
        for key in ("home", "bind", "public_url", "max_upload_mb", "url", "frontend", "backend", "backend_image"):
            self.assertIn(key + ":", text)
        self.assertNotRegex(text, r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net")   # no real tailnet name in a public repo


class TestCli(Tmp):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = main(list(argv), out=out)
        return code, out.getvalue(), err.getvalue()

    def test_config_prints_effective_values_and_sources(self):
        self.write("backend:\n  bind: 127.0.0.1:9100\n")
        code, out, _ = self.run_cli("config")
        self.assertEqual(code, 0)
        self.assertIn("backend.bind", out)
        self.assertIn("127.0.0.1:9100", out)
        self.assertIn("(file)", out)
        self.assertIn("(default)", out)
        self.assertIn(str(self.tmp / "relif.yaml"), out)

    def test_config_flag_and_invalid_file(self):
        p = self.write("deploy:\n  backend: k8s\n", "x.yaml")
        code, _, err = self.run_cli("--config", str(p), "config")
        self.assertEqual(code, 2)
        self.assertIn("deploy.backend", err)

    def test_home_comes_from_config(self):
        self.write(f"backend:\n  home: {self.tmp / 'store'}\n")
        code, out, _ = self.run_cli("init")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.tmp / "store").is_dir())

    def test_home_flag_beats_config(self):
        self.write(f"backend:\n  home: {self.tmp / 'store'}\n")
        code, _, _ = self.run_cli("--home", str(self.tmp / "flag"), "init")
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "flag").is_dir())
        self.assertFalse((self.tmp / "store").exists())


class TestDeploy(Tmp):
    def setUp(self):
        super().setUp()
        (self.tmp / "backend").mkdir()
        (self.tmp / "frontend").mkdir()
        for f in ("backend/compose.yaml", "backend/compose.tailscale.yaml", "frontend/build.py"):
            (self.tmp / f).write_text("")

    def dry(self, target, yaml_text=""):
        self.write(yaml_text)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = main(["deploy", target, "--dry-run"], out=out)
        return code, out.getvalue() + err.getvalue()

    def test_backend_docker(self):
        code, out = self.dry("backend", "backend:\n  bind: 127.0.0.1:9000\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f"docker compose -f {self.tmp / 'backend/compose.yaml'} up -d --build", out)
        self.assertNotIn("compose.tailscale", out)
        self.assertIn("RELIF_PORT=9000", out)

    def test_backend_docker_tailscale(self):
        code, out = self.dry("backend", "deploy:\n  backend: docker+tailscale\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f"-f {self.tmp / 'backend/compose.yaml'} -f {self.tmp / 'backend/compose.tailscale.yaml'}", out)

    def test_backend_local_runs_serve(self):
        code, out = self.dry("backend", "deploy:\n  backend: local\n")
        self.assertEqual(code, 0, out)
        self.assertIn("relif", out)
        self.assertIn("serve", out)
        self.assertNotIn("docker", out)

    def test_backend_image_is_pulled_not_built(self):
        code, out = self.dry("backend", "deploy:\n  backend_image: ghcr.io/me/relif-backend:latest\n")
        self.assertEqual(code, 0, out)
        self.assertIn("RELIF_IMAGE=ghcr.io/me/relif-backend:latest", out)
        self.assertIn("up -d --pull always", out)
        self.assertNotIn("--build", out)

    def test_no_backend_image_builds_locally(self):
        code, out = self.dry("backend")
        self.assertNotIn("RELIF_IMAGE", out)
        self.assertIn("--build", out)

    def test_bad_backend_image_is_named(self):
        self.write("deploy:\n  backend_image: 'not an image'\n")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(self.tmp / "relif.yaml")
        self.assertIn("deploy.backend_image", str(cm.exception))

    def test_frontend_vercel_builds_site_and_says_push(self):
        code, out = self.dry("frontend")
        self.assertEqual(code, 0, out)
        self.assertIn(f"{self.tmp / 'frontend/build.py'} --out {self.tmp / 'frontend/site'}", out)
        self.assertIn("Vercel", out)

    def test_frontend_none(self):
        code, out = self.dry("frontend", "deploy:\n  frontend: none\n")
        self.assertEqual(code, 0)
        self.assertIn("nothing to deploy", out.lower())

    def test_needs_a_relif_yaml_to_find_the_repo(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = main(["deploy", "backend", "--dry-run"], out=out)
        self.assertEqual(code, 2)
        self.assertIn("relif.yaml", err.getvalue())

    def test_runs_the_command_without_dry_run(self):
        self.write("deploy:\n  frontend: vercel\n")
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            code = main(["deploy", "frontend"], out=io.StringIO())
        self.assertEqual(code, 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:], [str(self.tmp / "frontend/build.py"), "--out", str(self.tmp / "frontend/site")])


if __name__ == "__main__":
    unittest.main()
