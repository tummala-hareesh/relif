import ast
import io
import json
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from relif import demo
from relif.cli import main
from relif.verdict import CAPITAL, CURRENT, NOT_DEDUCTIBLE, PARTIAL, UNSURE

ROOT = Path(__file__).resolve().parent.parent

# Golden table: every verdict branch, run through both `relif ask` and the demo page adapter.
GOLDEN = [
    ({"kind": "repair", "work": "restores"}, CURRENT),
    ({"kind": "repair", "work": "improves"}, CAPITAL),
    ({"kind": "repair", "work": "separate_asset"}, CAPITAL),
    ({"kind": "repair"}, UNSURE),
    ({"kind": "repair", "work": "unsure"}, UNSURE),
    ({"kind": "advertising"}, CURRENT),
    ({"kind": "insurance"}, CURRENT),
    ({"kind": "interest"}, CURRENT),
    ({"kind": "office"}, CURRENT),
    ({"kind": "professional"}, CURRENT),
    ({"kind": "management"}, CURRENT),
    ({"kind": "wages"}, CURRENT),
    ({"kind": "property_tax"}, CURRENT),
    ({"kind": "travel"}, CURRENT),
    ({"kind": "utilities"}, CURRENT),
    ({"kind": "vehicle"}, CURRENT),
    ({"kind": "appliance"}, CAPITAL),
    ({"kind": "improvement"}, CAPITAL),
    ({"kind": "fence"}, CAPITAL),
    ({"kind": "roof"}, UNSURE),
    ({"kind": "landscaping"}, UNSURE),
    ({"kind": "other"}, UNSURE),
    ({"kind": "loan_payment", "loan_part": "interest"}, CURRENT),
    ({"kind": "loan_payment", "loan_part": "principal"}, NOT_DEDUCTIBLE),
    ({"kind": "loan_payment"}, UNSURE),
    ({"kind": "own_labour"}, NOT_DEDUCTIBLE),
    ({"kind": "penalty"}, NOT_DEDUCTIBLE),
    ({"kind": "land_transfer_tax"}, NOT_DEDUCTIBLE),
    ({}, UNSURE),
    ({"kind": "utilities", "use": "personal"}, NOT_DEDUCTIBLE),
    ({"kind": "utilities", "available": "no"}, UNSURE),
    ({"kind": "utilities", "available": "unsure"}, UNSURE),
    ({"kind": "utilities", "profit": "no"}, NOT_DEDUCTIBLE),
    ({"kind": "utilities", "profit": "unsure"}, UNSURE),
    ({"kind": "utilities", "short_term": True, "str_compliant": "no"}, NOT_DEDUCTIBLE),
    ({"kind": "utilities", "short_term": True}, UNSURE),
    ({"kind": "utilities", "short_term": True, "str_compliant": "yes"}, CURRENT),
    ({"kind": "utilities", "use": "shared", "rental_pct": 60}, PARTIAL),
    ({"kind": "utilities", "use": "shared", "rental_pct": 0}, NOT_DEDUCTIBLE),
    ({"kind": "vehicle", "personal_pct": 30}, PARTIAL),
    ({"kind": "vehicle", "personal_pct": 100}, NOT_DEDUCTIBLE),
    ({"kind": "appliance", "use": "shared", "rental_pct": 50}, CAPITAL),
    ({"kind": "repair", "work": "restores", "use": "shared", "rental_pct": 60, "personal_pct": 25}, PARTIAL),
]


def cli_flags(form):
    flags = []
    for key, value in form.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            flags.append(flag)
        else:
            flags += [flag, str(value)]
    return flags


class GoldenTest(unittest.TestCase):
    """The page must say exactly what `relif ask` says, for every row."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="relif-demo-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.home = str(tmp / "home")
        self.assertEqual(main(["--home", self.home, "init"], out=io.StringIO()), 0)

    def cli_ask(self, form):
        out = io.StringIO()
        with redirect_stderr(io.StringIO()):
            code = main(["--home", self.home, "ask", *cli_flags(form)], out=out)
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue().splitlines()

    def test_page_matches_cli_for_every_row(self):
        for form, outcome in GOLDEN:
            with self.subTest(form=form):
                result = demo.ask(form)
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["lines"], self.cli_ask(form))
                self.assertEqual(result["lines"][0], result["label"])

    def test_golden_table_covers_every_kind_and_outcome(self):
        from relif.verdict import KINDS
        self.assertEqual({f.get("kind") for f, _ in GOLDEN} - {None}, set(KINDS))
        self.assertEqual({o for _, o in GOLDEN}, {CURRENT, CAPITAL, PARTIAL, NOT_DEDUCTIBLE, UNSURE})


class AskTest(unittest.TestCase):
    def test_widget_strings_are_accepted(self):
        result = demo.ask({"kind": "utilities", "use": "shared", "rental_pct": "60", "short_term": False})
        self.assertEqual(result["outcome"], PARTIAL)

    def test_blank_optional_answers_mean_not_given(self):
        self.assertEqual(demo.ask({"kind": "repair", "work": ""})["outcome"], UNSURE)

    def test_bad_answers_become_an_error_not_a_crash(self):
        result = demo.ask({"kind": "spaceship"})
        self.assertEqual(result["outcome"], "error")
        self.assertIn("kind must be one of", result["label"])
        self.assertEqual(demo.ask({"rental_pct": "lots"})["outcome"], "error")

    def test_unknown_fields_are_rejected(self):
        self.assertEqual(demo.ask({"amount": "42"})["outcome"], "error")

    def test_every_answer_carries_the_disclaimer(self):
        self.assertIn("Not tax advice", demo.ask({"kind": "repair", "work": "restores"})["lines"][-1])


class PageTest(unittest.TestCase):
    def setUp(self):
        self.html = demo.build_html(ROOT)
        m = re.search(r'<script type="application/json" id="relif-files">(.*?)</script>', self.html, re.S)
        self.assertIsNotNone(m)
        self.files = json.loads(m.group(1))

    def test_page_embeds_the_repo_sources_unchanged(self):
        self.assertEqual(set(self.files), {"app.py", "relif/__init__.py", "relif/verdict.py", "relif/demo.py"})
        self.assertEqual(self.files["relif/verdict.py"], (ROOT / "relif/verdict.py").read_text())
        self.assertEqual(self.files["app.py"], (ROOT / "demo/app.py").read_text())

    def test_embedded_json_cannot_close_the_script_tag(self):
        self.assertEqual(self.html.count("</script>"), 2)

    def test_only_the_pinned_stlite_cdn_is_loaded(self):
        hosts = set(re.findall(r'https?://([^/"\'\s)]+)', self.html.split('id="relif-files"')[0]))
        self.assertEqual(hosts, {"cdn.jsdelivr.net"})
        self.assertIn(f"@stlite/browser@{demo.STLITE_VERSION}/", self.html)
        self.assertRegex(demo.STLITE_VERSION, r"^\d+\.\d+\.\d+$")

    def test_page_keeps_nothing_in_browser_storage(self):
        script = self.html.split('id="relif-files"')[0]
        for word in ("idbfs", "localStorage", "indexedDB", "sessionStorage"):
            self.assertNotIn(word.lower(), script.lower())
        self.assertIn("Not tax advice", self.html)

    def test_app_only_imports_streamlit_and_shipped_modules(self):
        tree = ast.parse(self.files["app.py"])
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module)
        self.assertEqual(names, {"streamlit", "relif.demo", "relif.verdict"})

    def test_shipped_modules_use_the_stdlib_only(self):
        for path in ("relif/verdict.py", "relif/demo.py"):
            tree = ast.parse(self.files[path])
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(node.module.split(".")[0], {"__future__", "dataclasses", "typing", "json",
                                                              "pathlib", "relif", "argparse", "sys"}, path)
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("relif."):
                    self.assertIn(node.module, {"relif.verdict"}, path)

    def test_build_writes_index_html(self):
        out = Path(tempfile.mkdtemp(prefix="relif-site-"))
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        self.assertEqual(demo.main(["build", "--out", str(out / "site")]), 0)
        self.assertEqual((out / "site" / "index.html").read_text(), self.html)
        self.assertTrue((out / "site" / ".nojekyll").exists())


if __name__ == "__main__":
    unittest.main()


class FakeStreamlit:
    """Just enough of the streamlit API to run demo/app.py top to bottom without installing it."""

    def __init__(self, picks):
        self.picks, self.shown = picks, []

    def __getattr__(self, name):          # title, caption, set_page_config, markdown, ...
        return lambda *a, **k: self.shown.append((name, a[0] if a else None))

    def _pick(self, label, options, default):
        for key, value in self.picks.items():
            if label.startswith(key):
                assert value in options, (label, value)
                return value
        return default

    def radio(self, label, options, **k):
        return self._pick(label, options, options[0])

    def selectbox(self, label, options, format_func=str, **k):
        for o in options:
            format_func(o)
        return self._pick(label, options, options[0])

    def slider(self, label, lo, hi, value, **k):
        return self._pick(label, range(lo, hi + 1), value)

    def checkbox(self, label, **k):
        return self._pick(label, (True, False), False)


class AppSmokeTest(unittest.TestCase):
    def run_app(self, **picks):
        import sys
        st = FakeStreamlit(picks)
        old = sys.modules.get("streamlit")
        sys.modules["streamlit"] = st
        try:
            exec(compile((ROOT / "demo/app.py").read_text(), "app.py", "exec"), {"__name__": "__main__"})
        finally:
            if old is None:
                del sys.modules["streamlit"]
            else:
                sys.modules["streamlit"] = old
        return st.shown

    def test_app_shows_the_same_verdict_as_the_adapter(self):
        shown = self.run_app(**{"3.": "repair", "5.": "restores"})
        self.assertIn(("success", demo.ask({"kind": "repair", "work": "restores"})["label"]), shown)

    def test_app_runs_for_every_kind_and_branch(self):
        from relif.verdict import KINDS
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertTrue(any(n in ("success", "info", "error", "warning") for n, _ in self.run_app(**{"3.": kind})))
        shown = self.run_app(**{"1.": "shared", "Rented share": 60, "3.": "utilities", "7.": True, "Does it": "yes"})
        self.assertIn(("success", "Partly deductible: 60% under Utilities (9220)"), shown)
