"""CI/CD (issue #12): one workflow tests, deploys the frontend to Vercel and publishes the backend image to GHCR.

The backend never runs on Vercel (its ledger and receipts stay on the owner's machine): CI only builds the
Docker image, and `relif deploy backend` on the laptop pulls it.
"""
import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows/ci.yml"


def load():
    wf = yaml.safe_load(WF.read_text())
    wf["on"] = wf.pop(True, wf.get("on"))   # YAML 1.1 reads the bare key `on` as True
    return wf


def run_lines(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.wf = load()
        self.jobs = self.wf["jobs"]

    def test_one_workflow_replaces_test_yml(self):
        self.assertFalse((ROOT / ".github/workflows/test.yml").exists())
        self.assertIn("pull_request", self.wf["on"])
        self.assertEqual(self.wf["on"]["push"]["branches"], ["main"])
        self.assertIn("workflow_dispatch", self.wf["on"])   # megastar-deploy rerun starts a fresh run

    def test_tests_run_on_both_pythons_with_pyyaml(self):
        t = self.jobs["test"]
        self.assertEqual(t["strategy"]["matrix"]["python"], ["3.10", "3.13"])
        self.assertIn("pyyaml", run_lines(t))
        self.assertIn("python3 -m unittest discover -s tests -t .", run_lines(t))

    def test_read_only_by_default(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read"})

    def test_frontend_deploys_to_vercel_after_tests(self):
        f = self.jobs["frontend"]
        self.assertEqual(f["needs"], "test")
        lines = run_lines(f)
        for needle in ("vercel pull", "vercel build", "vercel deploy --prebuilt"):
            self.assertIn(needle, lines)
        self.assertIn("--prod", lines)                       # main → production
        self.assertIn("refs/heads/main", lines)              # …only on main; PRs get previews
        self.assertRegex(json.dumps(f), r"vercel@\d+\.\d+\.\d+")   # pinned CLI version
        for secret in ("VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"):
            self.assertIn(f"secrets.{secret}", json.dumps(f))

    def test_frontend_skips_cleanly_until_vercel_is_set_up_and_never_for_forks(self):
        cond = self.jobs["frontend"]["if"]
        self.assertIn("head.repo.full_name == github.repository", cond)
        self.assertIn("VERCEL_TOKEN", json.dumps(self.jobs["frontend"]))
        self.assertIn("skip", run_lines(self.jobs["frontend"]).lower())

    def test_frontend_posts_a_vercel_commit_status_for_megastar_deploy(self):
        f = self.jobs["frontend"]
        self.assertEqual(f["permissions"].get("statuses"), "write")
        lines = run_lines(f)
        self.assertIn("/statuses/", lines)
        self.assertIn("context=Vercel", lines)

    def test_backend_image_goes_to_ghcr_only_from_main(self):
        b = self.jobs["backend"]
        self.assertEqual(b["needs"], "test")
        self.assertIn("refs/heads/main", b["if"])
        self.assertEqual(b["permissions"], {"contents": "read", "packages": "write"})
        dump = json.dumps(b)
        self.assertIn("ghcr.io/", dump)
        self.assertIn("docker/build-push-action", dump)
        self.assertIn("backend", dump)                      # build context is backend/
        self.assertNotIn("vercel", dump.lower())            # the ledger never runs on Vercel

    def test_vercel_git_auto_deploys_are_off_so_the_workflow_is_the_only_path(self):
        cfg = json.loads((ROOT / "vercel.json").read_text())
        self.assertEqual(cfg.get("git"), {"deploymentEnabled": False})


if __name__ == "__main__":
    unittest.main()
