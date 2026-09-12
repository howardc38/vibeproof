"""A committed rename must preserve, rather than bypass, execution proof."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import shutil

ROOT = Path(__file__).resolve().parent.parent
APP = "def calculate(value):\n    return value - 2  # fixed deduction\n"
OLD_TEST = "import unittest\nfrom app import calculate\nclass Arithmetic(unittest.TestCase):\n    def test_old(self):\n        self.assertEqual(calculate(5), 3)\n"
NEW_TEST = OLD_TEST.replace("test_old", "test_difference")


class CommittedRename(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="v4 rename ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Rename regression")
        self.git("config", "user.email", "rename@example.invalid")
        (self.root / ".gitignore").write_text("__pycache__/\n")
        (self.root / "app.py").write_text(APP)
        (self.root / "test_app.py").write_text(OLD_TEST)
        self.before = self.commit("original name")
        (self.root / "test_app.py").write_text(NEW_TEST)
        self.renamed = self.commit("rename only")

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0",
                                        *args], cwd=self.root, text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def subject(self):
        return {"repo_root": str(self.root), "file": "test_app.py", "symbol": "test_old",
                "params": {"closing_test": "test_app.py",
                           "test_one_file_command": [sys.executable, "-m", "unittest", "test_app"],
                           "mutation_file": "app.py",
                           "mutation_gone": "    return value - 2  # fixed deduction",
                           "mutation_now": "    return value + 2  # deliberately wrong",
                           "rename_commits": [self.renamed]}}

    def checker(self, subject):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d) / "subject.json", Path(d) / "out.json"
            inp.write_text(json.dumps(subject))
            r = subprocess.run([sys.executable, str(ROOT / "checkers/review_finding.py"),
                                "--subject", str(inp), "--out", str(out)], cwd=self.root,
                               capture_output=True, text=True,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=60)
            return r, json.loads(out.read_text()) if out.exists() else None

    def test_real_rename_still_requires_and_observes_all_three_proofs(self):
        r, out = self.checker(self.subject())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(out["red_failed_at_parent"])
        self.assertTrue(out["green_passed_at_head"])
        self.assertTrue(out["symbol_executed"])
        self.assertGreater(out["calls"], 0)
        self.assertEqual(out["execution_target"]["original_symbol"], "test_old")
        self.assertEqual(out["execution_target"]["symbol"], "test_difference")
        self.assertEqual(self.git("diff", "--stat", "HEAD"), "")

    def resolve(self, commits=None):
        from kernel import review_renames
        return review_renames.resolve_rename(self.root, "test_app.py", "test_old",
                                      [self.renamed] if commits is None else commits)

    def test_missing_rename_proof_does_not_silently_follow_a_name(self):
        subject = self.subject()
        del subject["params"]["rename_commits"]
        r, out = self.checker(subject)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(out["symbol_executed"])

    def test_parent_before_rename_cannot_use_an_import_error_as_red(self):
        self.git("checkout", "--detach", self.before)
        (self.root / "app.py").write_text(APP.replace("calculate", "compute"))
        (self.root / "test_app.py").write_text(OLD_TEST.replace("calculate", "compute"))
        rename = self.commit("rename correct code; there is no behavioral fix")
        subject = self.subject()
        subject.update(file="app.py", symbol="calculate")
        params = subject["params"]
        for key in ("mutation_file", "mutation_gone", "mutation_now"):
            del params[key]
        params.update(parent_commit=self.before, rename_commits=[rename])
        r, _ = self.checker(subject)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_parent_after_rename_still_proves_a_real_behavior_fix(self):
        self.git("checkout", "--detach", self.before)
        (self.root / "app.py").write_text(APP.replace("value - 2", "value + 2"))
        self.commit("actual broken calculation")
        (self.root / "app.py").write_text(APP.replace("calculate", "compute").replace("value - 2", "value + 2"))
        (self.root / "test_app.py").write_text(OLD_TEST.replace("calculate", "compute"))
        rename = self.commit("rename before the behavior repair")
        (self.root / "app.py").write_text(APP.replace("calculate", "compute"))
        self.commit("repair subtraction")
        subject = self.subject()
        subject.update(file="app.py", symbol="calculate")
        params = subject["params"]
        for key in ("mutation_file", "mutation_gone", "mutation_now"):
            del params[key]
        params.update(parent_commit=rename, rename_commits=[rename])
        r, out = self.checker(subject)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(out["red_failed_at_parent"] and out["green_passed_at_head"] and out["symbol_executed"])
        self.assertEqual(out["execution_target"]["parent_commit"], rename)

    def test_two_committed_renames_are_followed_in_order(self):
        (self.root / "test_app.py").write_text(NEW_TEST.replace("test_difference", "test_deduction"))
        again = self.commit("second rename")
        proof = self.resolve([self.renamed, again])
        self.assertEqual(proof["symbol"], "test_deduction")
        self.assertEqual([s["from"] for s in proof["renames"]], ["test_old", "test_difference"])
        with self.assertRaises(ValueError):
            self.resolve([again, self.renamed])

    def test_body_can_evolve_after_a_proven_rename(self):
        (self.root / "test_app.py").write_text(NEW_TEST.replace("calculate(5), 3", "calculate(7), 5"))
        self.commit("a later legitimate test improvement")
        self.assertEqual(self.resolve()["symbol"], "test_difference")

    def test_body_change_cannot_be_passed_off_as_the_rename(self):
        self.git("checkout", "--detach", self.before)
        (self.root / "test_app.py").write_text(NEW_TEST.replace("calculate(5), 3", "calculate(7), 5"))
        bad = self.commit("rename and change body together")
        with self.assertRaisesRegex(ValueError, "unique rename"):
            self.resolve([bad])

    def test_an_unrelated_existing_function_is_not_a_rename(self):
        self.git("checkout", "--detach", self.before)
        both = OLD_TEST + "    def test_difference(self):\n        self.assertEqual(calculate(5), 3)\n"
        (self.root / "test_app.py").write_text(both)
        self.commit("two distinct functions")
        (self.root / "test_app.py").write_text(NEW_TEST)
        deleted = self.commit("delete one; do not rename it")
        with self.assertRaisesRegex(ValueError, "unique rename"):
            self.resolve([deleted])

    def test_two_equivalent_new_functions_are_ambiguous(self):
        self.git("checkout", "--detach", self.before)
        (self.root / "test_app.py").write_text(NEW_TEST + "    def test_other(self):\n        self.assertEqual(calculate(5), 3)\n")
        bad = self.commit("two possible successors")
        with self.assertRaisesRegex(ValueError, "unique rename"):
            self.resolve([bad])

    def test_scope_or_signature_changes_are_not_pure_renames(self):
        for text in (NEW_TEST.replace("Arithmetic", "Other"), NEW_TEST.replace("(self):", "(self, extra=0):")):
            with self.subTest(text=text):
                self.git("checkout", "--detach", self.before)
                (self.root / "test_app.py").write_text(text)
                bad = self.commit("not just a rename")
                with self.assertRaises(ValueError):
                    self.resolve([bad])

    def test_nonancestor_and_root_commits_are_rejected(self):
        self.git("checkout", "--detach", self.before)
        with self.assertRaises(ValueError):
            self.resolve([self.renamed])
        self.git("checkout", "--detach", self.renamed)
        with self.assertRaisesRegex(ValueError, "one parent"):
            self.resolve([self.before])

    def test_malformed_duplicate_and_unknown_history_is_rejected(self):
        for commits in ([], "HEAD", [False], [""], ["missing-rename-commit"], [self.renamed, self.renamed]):
            with self.subTest(commits=commits), self.assertRaises(ValueError):
                self.resolve(commits)

    def test_a_disappeared_or_restored_original_target_is_rejected(self):
        for text in ("import unittest\n", NEW_TEST + "\ndef test_old():\n    pass\n"):
            (self.root / "test_app.py").write_text(text)
            with self.assertRaises(ValueError):
                self.resolve()

    def test_renaming_does_not_waive_execution_or_mutation_detection(self):
        subject = self.subject()
        subject["params"]["test_one_file_command"] = [sys.executable, "-c", "import app; assert app.calculate(5)==3"]
        r, out = self.checker(subject)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(out["red_failed_at_parent"])
        self.assertTrue(out["green_passed_at_head"])
        self.assertFalse(out["symbol_executed"])
        subject = self.subject()
        subject["params"]["mutation_now"] = "    return value - 2  # still the correct answer"
        r, out = self.checker(subject)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(out["symbol_executed"])
        self.assertFalse(out["red_failed_at_parent"])

    def test_the_checker_rejects_forged_or_text_only_rename_payloads(self):
        for params in ({"rename_commits": "HEAD"}, {"rename_commits": []},
                       {"text_gone": "some sufficiently long old sentence"}):
            subject = self.subject()
            subject["params"].update(params)
            r, _ = self.checker(subject)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def ledger_fixture(self):
        from kernel import config, hashing, ledger
        (self.root / ".v4").mkdir()
        (self.root / "checkers").mkdir()
        checker = self.root / "checkers/review_finding.py"
        shutil.copyfile(ROOT / "checkers/review_finding.py", checker)
        (self.root / ".v4/config.json").write_text(json.dumps({"test_command": "python3 -m unittest test_app", "policy": "allow_accepted_risk"}))
        (self.root / ".v4/claim_kinds.json").write_text(json.dumps({"review-finding": {"checker": "review-finding", "question_template": "q", "staleness": "subject"}}))
        (self.root / ".v4/checkers.json").write_text(json.dumps({"review-finding": {"path": "checkers/review_finding.py", "sha256": hashing.file_sha(checker), "reads": ["**"]}}))
        self.commit("test harness configuration")
        c = ledger.connect(self.root)
        self.addCleanup(c.close)
        ledger.insert(c, "task", id="t", request="prove the renamed test still checks the calculation", scope_globs=["test_app.py"], base_commit=self.before, created_at="2026")
        cid = hashing.claim_id("t", "review-finding", "test_app.py", "test_old", "")
        ledger.insert(c, "claim", id=cid, task_id="t", kind="review-finding", question="q", subject_refs=[{"kind": "file", "path": "test_app.py"}], checker="review-finding", origin="review", file="test_app.py", symbol="test_old", created_at="2026")
        return c, config.RepoConfig(self.root), cid

    def bind(self, c, cid, **changes):
        from kernel import review
        params = self.subject()["params"]
        args = dict(claim_id=cid, root=self.root, test_path="test_app.py",
                    command=params["test_one_file_command"], rename_commits=[self.renamed],
                    mutation=("app.py", params["mutation_gone"], params["mutation_now"]))
        args.update(changes)
        return review.bind_closing_test(c, **args)

    def test_producer_preserves_claim_identity_and_refuses_invalid_history_without_writing(self):
        c, _, cid = self.ledger_fixture()
        before = dict(c.execute("SELECT * FROM claim WHERE id=?", (cid,)).fetchone())
        self.bind(c, cid)
        self.assertEqual(dict(c.execute("SELECT * FROM claim WHERE id=?", (cid,)).fetchone()), before)
        count = c.execute("SELECT count(*) FROM event WHERE kind='review_close'").fetchone()[0]
        with self.assertRaises(ValueError):
            self.bind(c, cid, rename_commits=[self.before])
        self.assertEqual(c.execute("SELECT count(*) FROM event WHERE kind='review_close'").fetchone()[0], count)

    def test_changed_or_removed_rename_binding_cannot_keep_an_old_pass(self):
        from kernel import lifecycle, review, state
        c, cfg, cid = self.ledger_fixture()
        self.bind(c, cid)
        lifecycle.check(c, cfg, "t", only=[cid])
        self.assertFalse(lifecycle.report(c, cfg, "t")[1])
        stored = json.loads(c.execute("SELECT subject_digest FROM attempt ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertIn(review.RENAME_BINDING_KEY, stored)
        self.bind(c, cid, command=[sys.executable, "-c", "import app; assert app.calculate(5)==3"])
        row = c.execute("SELECT * FROM claim WHERE id=?", (cid,)).fetchone()
        self.assertEqual(state.claim_state(c, self.root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha, checker_sha_of=cfg.checker_sha_on_disk), state.STALE)
        self.bind(c, cid, rename_commits=None)
        self.assertEqual(state.claim_state(c, self.root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha, checker_sha_of=cfg.checker_sha_on_disk), state.STALE)
        lifecycle.check(c, cfg, "t", only=[cid])
        self.assertTrue(lifecycle.report(c, cfg, "t")[1])

    def test_cli_records_rename_commit_and_rejects_it_for_text_closure(self):
        c, _, cid = self.ledger_fixture()
        params = self.subject()["params"]
        args = [sys.executable, "-m", "kernel.cli", "--repo", str(self.root), "review", "close", "--claim", cid,
                "--test", "test_app.py", "--command", "python3 -m unittest {path}",
                "--mutation-file", "app.py", "--mutation-gone", params["mutation_gone"],
                "--mutation-now", params["mutation_now"], "--rename-commit", self.renamed]
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(c.execute("SELECT payload FROM event WHERE kind='review_close' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(payload["rename_commits"], [self.renamed])
        r = subprocess.run(args + ["--gone", "a sufficiently long text marker"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
