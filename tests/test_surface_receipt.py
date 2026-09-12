"""A runner's successful process is not evidence that its required tests ran."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SurfaceReceipt(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v4-surface-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".v4").mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def run_surface(self, body, **options):
        (self.root / "driver.py").write_text(body)
        cfg = {"surface_command": f"{sys.executable} driver.py", **options}
        (self.root / ".v4/config.json").write_text(json.dumps(cfg))
        subject = self.root / ".git/subject.json"
        subject.write_text(json.dumps({"repo_root": str(self.root)}))
        out = self.root / ".git/out.json"
        r = subprocess.run([sys.executable, str(ROOT / "checkers/surface_proof.py"),
                            "--subject", str(subject), "--out", str(out)],
                           capture_output=True, text=True)
        self.said = r.stdout + r.stderr
        self.evidence = json.loads(out.read_text()) if out.exists() else {}
        return r.returncode

    def driver(self, edit=""):
        return '''import json, os
from pathlib import Path
value = {"schema": 1, "run_id": os.environ["V4_SURFACE_RUN_ID"],
         "kind": "command", "cwd": str(Path.cwd()),
         "planned": ["readback"],
         "checks": [{"id": "readback", "status": "passed"}], "errors": []}
Path("state.txt").write_text("saved")
assert Path("state.txt").read_text() == "saved"
''' + edit + '\nPath(os.environ["V4_SURFACE_RESULT"]).write_text(json.dumps(value))\n'

    def test_output_only_and_silence_do_not_prove_execution(self):
        for body in ("print('8 passed')", "pass", "print('Listing 8 tests')"):
            with self.subTest(body=body):
                self.assertEqual(self.run_surface(body), 1, self.said)

    def test_required_execution_and_private_fresh_receipt(self):
        self.assertEqual(self.run_surface(self.driver(), surface_required=["readback"]), 0, self.said)
        first = self.evidence["receipt"]["run_id"]
        self.assertEqual(self.run_surface(self.driver()), 0, self.said)
        self.assertNotEqual(first, self.evidence["receipt"]["run_id"])
        self.assertTrue(Path(self.evidence["result_path"]).is_relative_to((self.root / ".git").resolve()))

    def test_no_execution_missing_required_stale_and_inconsistent_results(self):
        for edit in (
            'value["checks"][0]["status"] = "skipped"',
            'value["checks"] = []',
            'value["planned"] = []',
            'value["run_id"] = "previous-run"',
            'value["checks"] *= 2',
            'value["checks"][0]["id"] = "other"',
            'value["checks"][0]["status"] = "not_run"',
            'value["cwd"] = "/wrong-checkout"',
        ):
            with self.subTest(edit=edit):
                self.assertEqual(self.run_surface(self.driver(edit), surface_required=["readback"]), 1, self.said)
        self.assertEqual(self.run_surface(self.driver(), surface_required=["other"]), 1, self.said)

    def test_real_failure_and_report_process_disagreement_do_not_pass(self):
        for edit in ('value["checks"][0]["status"] = "failed"',
                     'value["errors"] = ["runner interrupted"]'):
            self.assertEqual(self.run_surface(self.driver(edit)), 1, self.said)
        self.assertEqual(self.run_surface(self.driver() + '\nraise SystemExit(1)\n'), 1, self.said)

    def test_browser_requires_declared_cases_navigation_and_owned_servers(self):
        browser = '''value["kind"] = "browser"
value["managed_servers"] = [{"cwd": str(Path.cwd()), "reuse": False}]
value["checks"][0]["browser"] = {"run_id": value["run_id"], "name": "chromium", "urls": ["http://localhost:8000/"]}
'''
        self.assertEqual(self.run_surface(self.driver(browser), surface_kind="browser"), 1, self.said)
        for edit in ('value["checks"][0].pop("browser")',
                     'value["checks"][0]["browser"]["urls"] = ["about:blank"]',
                     'value["managed_servers"] = []',
                     'value["managed_servers"][0]["reuse"] = True',
                     'value["managed_servers"][0]["cwd"] = "/another-repo"'):
            with self.subTest(edit=edit):
                self.assertEqual(self.run_surface(self.driver(browser + edit),
                    surface_kind="browser", surface_required=["readback"]), 1, self.said)


if __name__ == "__main__":
    unittest.main()
