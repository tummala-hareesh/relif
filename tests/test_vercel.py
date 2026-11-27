"""The frontend deploys on Vercel straight from this repo (issue #12): static build, no secrets, no second repo."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class VercelConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "vercel.json").read_text())

    def test_builds_the_static_frontend_with_the_stdlib_script(self):
        self.assertEqual(self.cfg["buildCommand"], "python3 frontend/build.py --out frontend/site")
        self.assertEqual(self.cfg["outputDirectory"], "frontend/site")
        self.assertIsNone(self.cfg["framework"])
        self.assertNotIn("pip", self.cfg.get("installCommand", ""))   # build.py needs no packages

    def test_no_serverless_functions_or_rewrites_to_a_backend(self):
        self.assertFalse((ROOT / "api").exists())   # the ledger never runs on Vercel
        for key in ("functions", "rewrites", "redirects", "routes"):
            self.assertNotIn(key, self.cfg)

    def test_sends_no_referrer_and_nosniff(self):
        headers = {h["key"]: h["value"] for block in self.cfg["headers"] for h in block["headers"]}
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_old_pages_deploy_is_gone(self):
        self.assertFalse((ROOT / ".github/workflows/demo-pages.yml").exists())
        readme = (ROOT / "README.md").read_text()
        for old in ("relif-demo", "DEMO_DEPLOY_KEY", "demo-v", "stays private"):
            self.assertNotIn(old, readme)

    def test_built_site_is_not_committed(self):
        self.assertIn("frontend/site/", (ROOT / ".gitignore").read_text().split())

    def test_readme_documents_config_and_deploy(self):
        readme = (ROOT / "README.md").read_text()
        for needle in ("relif.yaml", "relif deploy backend", "relif deploy frontend", "relif config", "Vercel"):
            self.assertIn(needle, readme)
        self.assertRegex(readme, re.escape("python3 -m unittest discover -s tests -t ."))


if __name__ == "__main__":
    unittest.main()
