"""The demo deploy pushes only the built site to the public relif-demo repo (issue #8).

Stdlib only, so the workflow is checked as text rather than parsed as YAML.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "demo-pages.yml"


class DemoWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        # Ignore comments so the checks only see real configuration.
        self.config = "\n".join(line.split("#", 1)[0] for line in self.text.splitlines())

    def test_runs_only_on_demo_tags_or_by_hand(self):
        self.assertIn('tags: ["demo-v*"]', self.config)
        self.assertIn("workflow_dispatch:", self.config)
        self.assertNotRegex(self.config, r"(?m)^\s*branches:")

    def test_tests_run_before_the_build_and_deploy(self):
        tests = self.config.index("python3 -m unittest discover -s tests")
        build = self.config.index("python3 -m relif.demo build --out site")
        deploy = self.config.index("peaceiris/actions-gh-pages@")
        self.assertLess(tests, build)
        self.assertLess(build, deploy)

    def test_deploys_the_site_to_the_public_demo_repo_with_a_deploy_key(self):
        self.assertIn("external_repository: tummala-hareesh/relif-demo", self.config)
        self.assertIn("publish_branch: gh-pages", self.config)
        self.assertIn("publish_dir: ./site", self.config)
        self.assertIn("deploy_key: ${{ secrets.DEMO_DEPLOY_KEY }}", self.config)
        self.assertIn("force_orphan: true", self.config)

    def test_needs_no_pages_permissions_on_the_private_repo(self):
        self.assertNotIn("pages: write", self.config)
        self.assertNotIn("id-token: write", self.config)
        self.assertNotIn("github-pages", self.config)
        self.assertNotIn("deploy-pages", self.config)
        self.assertNotIn("upload-pages-artifact", self.config)
        self.assertRegex(self.config, r"(?m)^\s*contents: read\s*$")

    def test_third_party_actions_that_see_the_deploy_key_are_pinned_to_a_commit(self):
        # Tags can be moved; the action handling DEMO_DEPLOY_KEY must be a full commit SHA.
        for action in re.findall(r"uses:\s*(\S+)", self.config):
            if not action.startswith("actions/"):
                self.assertRegex(action, r"@[0-9a-f]{40}$", action)

    def test_does_not_use_the_repo_token_for_the_push(self):
        self.assertNotRegex(self.config, r"github_token|personal_token|GITHUB_TOKEN")

    def test_readme_points_at_the_demo_url(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://tummala-hareesh.github.io/relif-demo/", readme)
        self.assertIn("DEMO_DEPLOY_KEY", readme)


if __name__ == "__main__":
    unittest.main()
