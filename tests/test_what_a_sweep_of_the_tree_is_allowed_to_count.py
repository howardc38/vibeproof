"""Repairs in `checkers/control_plane_budget.py`, `checkers/runtime_proof.py`,
`kernel/analysis/structural_lint.py`, `kernel/analysis/dangling_ref.py` and
`checkers/dead_wiring.py`.

    python3 -m unittest tests.test_what_a_sweep_of_the_tree_is_allowed_to_count -v

Five programs that walk a repo, and what each of them was counting that was
not the repo: bytes git never heard of, a directory the checkout happens to sit
under, an import inside `if TYPE_CHECKING:`, and the letters of a key rather
than the key.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "checkers"))

from kernel.analysis import dangling_ref, structural_lint  # noqa: E402

import control_plane_budget  # noqa: E402
import dead_wiring  # noqa: E402
import runtime_proof  # noqa: E402


def _repo(case, files, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    return tmp


class WhatCountsAgainstARepoOwnCeiling(unittest.TestCase):
    """`root.rglob("*.py")` filtered by nine hand-written directory names.

    It ignored `derive_exclude` and did not name `.v4`, so in an adopter --
    where `v4 install` copies the fixture sets to `.v4/fixtures/**` and the
    checkers into the tree -- every framework file and every deliberately
    broken red fixture was charged against that repo's own control plane.
    """

    def test_untracked_bytes_are_not_this_repos_control_plane(self):
        root = _repo(self, {"kernel/a.py": "x = 1\ny = 2\n"})
        before = sum(control_plane_budget.measure(root).values())
        junk = root / ".venv" / "lib"
        junk.mkdir(parents=True)
        (junk / "huge.py").write_text("z = 1\n" * 400)
        self.assertEqual(sum(control_plane_budget.measure(root).values()),
                         before)

    def test_what_the_repo_excludes_from_derivation_is_not_counted(self):
        root = _repo(self, {"kernel/a.py": "x = 1\n",
                            ".v4/fixtures/red/broken.py": "y = 1\n" * 50},
                     derive_exclude=[".v4/fixtures/**"])
        got = control_plane_budget.measure(root, subject={
            "params": {"derive_exclude": [".v4/fixtures/**"]}})
        self.assertNotIn(".v4", got, got)

    def test_a_checkout_under_a_directory_named_tests_still_measures(self):
        """The exclusion used to be applied to the absolute path, so a repo
        checked out anywhere under `tests/`, `docs/`, `build/` or `dist/`
        measured zero and passed forever -- the check that cannot fail, in the
        checker written to prevent exactly that."""
        outer = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outer, ignore_errors=True)
        home = outer / "docs" / "build" / "myrepo"
        home.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=home, capture_output=True)
        (home / ".v4").mkdir()
        (home / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (home / "kernel").mkdir()
        (home / "kernel" / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
        subprocess.run(["git", "add", "-A"], cwd=home, capture_output=True)
        self.assertGreater(sum(control_plane_budget.measure(home).values()), 0)


class AControlQueryObservesTheSameState(unittest.TestCase):
    """The old once-per-invocation optimization compared different states.

    The real SQLite multi-proof regression is in test_runtime_proof_contract;
    this checks the direct run_one caller also gets a post-trigger control.
    """

    def test_the_empty_query_is_its_own_step(self):
        root = _repo(self, {})
        got = runtime_proof.empty_query(root, "cat")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stdout, "")

    def test_run_one_asks_the_control_after_the_real_query(self):
        root = _repo(self, {})
        marker = root / "asked.txt"
        cmd = f"echo asked >> {marker}; cat >> {marker}; echo 1"
        runtime_proof.run_one(root, cmd,
                              {"name": "p", "trigger": "true",
                               "truth": "select {run_id};", "expect": "eq:1"},
                              run_id="rt1")
        self.assertEqual(marker.read_text(), "asked\nselect rt1;asked\n")


class OnePlaceSaysWhereTheProbeRepoIs(unittest.TestCase):
    """The override moved the repo the trigger writes and not the query.

    `tools/runtime_probe.sh` read `V4_RUNTIME_PROBE`, and `truth_command`
    hardcoded `${TMPDIR:-/tmp}/v4-runtime-probe`, so setting the variable left
    the query pointed at the old database -- and the failure arrived as "the
    truth owner disagreed" rather than "could not ask", because an earlier
    run's database is usually still there.
    """

    def test_the_trigger_and_the_query_follow_the_same_variable(self):
        import os
        probe = Path(tempfile.mkdtemp()) / "probe"
        self.addCleanup(shutil.rmtree, probe.parent, ignore_errors=True)
        cfg = json.loads((ROOT / ".v4" / "config.json").read_text())
        proof = dict(cfg["runtime_proof"][0])
        old = os.environ.get("V4_RUNTIME_PROBE")
        os.environ["V4_RUNTIME_PROBE"] = str(probe)
        try:
            ok, why = runtime_proof.run_one(
                ROOT, cfg["truth_command"], proof, run_id="rtfixture01")
        finally:
            if old is None:
                os.environ.pop("V4_RUNTIME_PROBE", None)
            else:
                os.environ["V4_RUNTIME_PROBE"] = old
        self.assertTrue(ok, why)
        self.assertTrue((probe / ".git" / "v4" / "ledger.db").is_file(),
                        "the query answered from a database the trigger never "
                        "wrote")


class AProofAboutTheChangeUnderReview(unittest.TestCase):
    """The list is repo-level and identical for every task.

    So every task got the same green from the same probe: claim
    285afb060f2092b7 on `repo-review` went ANSWERED exit 0 with "2 proof(s)
    triggered, and the truth owner agreed" while the review touched nothing
    that probe exercises -- PL-8, which this kind was built to close,
    reproducing its own shape.
    """

    def _main(self, root, subject):
        import contextlib
        import io
        sub = root / "subject.json"
        sub.write_text(json.dumps(subject))
        argv = sys.argv
        sys.argv = ["runtime_proof.py", "--subject", str(sub)]
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                code = runtime_proof.main()
        finally:
            sys.argv = argv
        return code, said.getvalue()

    def test_a_proof_that_covers_nothing_this_change_touched_cannot_verify(self):
        root = _repo(self, {"app/x.py": "x = 1\n", "other/y.py": "y = 1\n"},
                     runtime_proof=[{"name": "about other/",
                                     "covers": ["other/**"],
                                     "trigger": "true",
                                     "truth": "select '{run_id}';",
                                     "expect": "eq:1"}],
                     truth_command="cat")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "base"], cwd=root, capture_output=True)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        (root / "app" / "x.py").write_text("x = 2\n")
        code, said = self._main(root, {"repo_root": str(root), "diff_base": base})
        self.assertEqual(code, runtime_proof.UNSUPPORTED, said)
        self.assertIn("covers", said)


class ATriggerThatNeverSucceededIsNotADisagreement(unittest.TestCase):
    """Two failures wore one sentence, and both halves of it were false.

    A trigger exiting non-zero was appended to `failed`, which prints
    `N proof(s) ran and the truth owner disagreed` and closes with
    `The trigger succeeded and the row is not there`. Measured on an adopter:
    `curl ... exited 7` -- connection refused, no server -- under exactly that
    headline, on a task whose scope was `detectors/**` and touched no runtime
    code at all. The first line sends the reader to the database; the repair was
    to start a process.
    """

    def _main(self, root):
        import contextlib
        import io
        sub = root / "subject.json"
        sub.write_text(json.dumps({"repo_root": str(root)}))
        argv = sys.argv
        sys.argv = ["runtime_proof.py", "--subject", str(sub)]
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
                code = runtime_proof.main()
        finally:
            sys.argv = argv
        return code, said.getvalue()

    PROOF = {"name": "a write that lands", "trigger": "exit 7",
             "truth": "select '{run_id}';", "expect": "eq:1"}

    def test_the_headline_does_not_say_the_truth_owner_spoke(self):
        root = _repo(self, {"app/x.py": "x = 1\n"},
                     runtime_proof=[self.PROOF], truth_command="cat")
        code, said = self._main(root)
        self.assertEqual(code, runtime_proof.FAIL, said)
        self.assertNotIn("the truth owner disagreed", said)
        self.assertNotIn("The trigger succeeded", said)
        self.assertIn("never ran", said)
        self.assertIn("exited 7", said)

    def test_run_one_gives_it_its_own_verdict(self):
        root = _repo(self, {}, truth_command="cat")
        ok, why = runtime_proof.run_one(root, "cat", self.PROOF, run_id="rt1")
        self.assertIs(ok, runtime_proof.NOT_TRIGGERED)
        self.assertIsNot(ok, False, "False is read as `the truth owner disagreed`")

    def test_a_real_disagreement_still_says_so(self):
        """The other half of the split, so the repair cannot quietly swallow
        the finding this kind exists for."""
        root = _repo(self, {"app/x.py": "x = 1\n"},
                     runtime_proof=[{"name": "a write that lands",
                                     "trigger": "true",
                                     "truth": "select '{run_id}';",
                                     "expect": "eq:1"}],
                     truth_command="printf '0\\n'")
        code, said = self._main(root)
        self.assertEqual(code, runtime_proof.FAIL, said)
        self.assertIn("the truth owner disagreed", said)


class WhatAModuleBindsInsideAConditional(unittest.TestCase):
    """The `if` branch collected classes, functions and assignments -- not imports.

    An import is the commonest thing inside `if TYPE_CHECKING:`; it is what the
    block is for. So a module importing `Bar` there, and another module naming
    it, got "the code no longer has it" from the checker whose whole job is to
    be believed.
    """

    def _defined(self, body):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = tmp / "m.py"
        p.write_text(body)
        return dangling_ref._defined(p)

    def test_an_import_under_type_checking_is_a_binding(self):
        got = self._defined("from typing import TYPE_CHECKING\n"
                            "if TYPE_CHECKING:\n    from other import Bar\n")
        self.assertIn("Bar", got)

    def test_and_a_platform_branch_binds_both_ways(self):
        got = self._defined("import sys\n"
                            "if sys.platform == 'win32':\n"
                            "    import msvcrt as _io\n"
                            "else:\n    import termios as _io\n")
        self.assertIn("_io", got)

    def test_an_annotated_assignment_inside_one_too(self):
        got = self._defined("if True:\n    LIMIT: int = 5\n")
        self.assertIn("LIMIT", got)


class WhatTheDanglingScanWalks(unittest.TestCase):
    """A fourth hand-rolled repo walk, with a bare `if True:` inside it.

    `rglob("*.py")` with an inline skip of three directory names, in the module
    whose docstring is about a checker whose whole job is to be believed -- and
    none of the four copies of that walk honoured `derive_exclude`.
    """

    def test_an_import_of_a_name_the_module_does_not_bind_is_found(self):
        root = _repo(self, {
            "pkg/__init__.py": "",
            "pkg/other.py": "def here():\n    return 1\n",
            "pkg/user.py": "from pkg.other import gone\n"})
        got = dangling_ref.scan(root)
        self.assertTrue([g for g in got if "gone" in g], got)

    def test_and_what_the_repo_ignores_is_not_walked(self):
        """The hand-written skip list named `__pycache__`, `.venv` and
        `node_modules`; git already knows, and it knows about `vendor/`,
        `target/` and `.tox/` too."""
        root = _repo(self, {"pkg/__init__.py": "", "pkg/a.py": "x = 1\n",
                            ".gitignore": "vendor/\n"})
        junk = root / "vendor" / "lib"
        junk.mkdir(parents=True)
        (junk / "third_party.py").write_text("from pkg.a import missing\n")
        got = dangling_ref.scan(root)
        self.assertEqual([g for g in got if "third_party" in str(g)], [], got)

    def test_and_so_is_what_derive_exclude_names(self):
        root = _repo(self, {"pkg/__init__.py": "", "pkg/a.py": "x = 1\n",
                            ".v4/fixtures/red/broken.py":
                                "from pkg.a import missing\n"},
                     derive_exclude=[".v4/fixtures/**"])
        got = dangling_ref.scan(root, subject={
            "params": {"derive_exclude": [".v4/fixtures/**"]}})
        self.assertEqual([g for g in got if "fixtures" in str(g)], [], got)


class ThreeAnswersToSplittingAgainstABaseline(unittest.TestCase):
    """One name, two arities: a reader who learned `partition` from
    `checkers/test_shape.py` unpacked two values here and lost `stale`."""

    def test_the_lints_split_returns_the_same_three(self):
        new, carried, stale = structural_lint.partition(
            [], set(), renames={})
        self.assertEqual((new, carried, stale), ([], [], []))


class AKeyIsReadAsAKeyNotAsLetters(unittest.TestCase):
    """`if key not in control_src` over every source file concatenated.

    Any key whose name is a common substring passed unconditionally -- and this
    is the check that is supposed to notice a config key nothing reads.
    """

    def test_a_name_that_only_appears_inside_a_word_is_not_a_reader(self):
        self.assertFalse(dead_wiring._names(
            "mode", "def f():\n    return the_model.name  # modes\n"))

    def test_a_quoted_key_is(self):
        self.assertTrue(dead_wiring._names(
            "mode", 'v = cfg.get("mode")\n'))
        self.assertTrue(dead_wiring._names(
            "mode", "v = declared(cfg, 'mode')\n"))

    def test_the_check_itself_says_which_key_nothing_reads(self):
        root = _repo(self, {"kernel/ledger.py": "SCHEMA = ''\n"},
                     thresholds={"zzq_dial_nothing_turns": 3})
        got = dead_wiring.check(root)
        self.assertTrue([p for p in got if "zzq_dial_nothing_turns" in p], got)

    def test_the_facts_table_is_this_repos_own(self):
        root = _repo(self, {"kernel/ledger.py": "SCHEMA = ''\n"})
        (root / ".v4" / "facts.aaa_other.json").write_text(json.dumps(
            {"repo": "aaa_other", "dal_globs": ["theirs/**"]}))
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps(
            {"repo": root.name, "dal_globs": ["mine/**"]}))
        self.assertEqual(dead_wiring._facts(root).get("dal_globs"), ["mine/**"])

    def test_a_claim_line_written_where_nothing_parses_it_is_reported(self):
        root = _repo(self, {
            "kernel/ledger.py": "SCHEMA = ''\n",
            ".claude/commands/run.md": "Then emit:\n\n```\n"
                                       "V4-CLAIM: kind=review-finding file=a.py\n```\n"})
        got = dead_wiring._claim_lines_have_a_reader(root)
        self.assertTrue([p for p in got if "run.md" in p], got)


class AGoFileNobodyCouldReadIsNotAGoFileThatResolves(unittest.TestCase):
    """`go_scan` answered every failure of `gosource.shape` with `continue`.

    `shape` returns `None` for a helper that would not build, a build that
    timed out and a `go` that is not installed, so a run where nothing could be
    read returned `[]` -- and `checkers/dangling_ref.py` printed "every
    same-repo reference resolves" and exited 0 about files it never opened.

    Measured 2026-08-27: a test in this suite unlinked every `v4-go-*` binary
    in the system temp directory, so every later caller paid a cold `go build`,
    and under a loaded `v4 accept` that is what times out. The trigger was the
    test; the silence was here, and the silence is what this pins.
    """

    def _repo(self, *, ignored=True):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
        (root / "app").mkdir()
        # `//go:build ignore`, the one form `_GO_IGNORED` matches -- a file
        # under any other constraint is one the compiler reads, and the
        # compiler's answer is not this checker's question.
        head = "//go:build ignore\n\n" if ignored else ""
        (root / "app" / "h.go").write_text(
            f"{head}package app\n\nimport \"example.com/m/lib\"\n\n"
            "func H() { lib.Gone() }\n")
        (root / "lib").mkdir()
        (root / "lib" / "l.go").write_text("package lib\n\nfunc Here() {}\n")
        return root

    def test_a_shape_that_will_not_read_comes_back_as_unread(self):
        """`shape` returning None is the one thing every failure looks like."""
        from kernel.analysis import gosource
        root = self._repo()
        real, gosource.shape = gosource.shape, lambda _p: None
        self.addCleanup(setattr, gosource, "shape", real)
        # Named, not tracked: `go_scan` falls back to git for the file
        # list, and this fixture is a directory rather than a checkout.
        found, unread = dangling_ref.go_scan(root, ["app/h.go"])
        self.assertEqual(found, [])
        self.assertTrue(unread, "a file that could not be read said nothing")
        self.assertIn("app/h.go", " ".join(unread))

    def test_and_the_checker_calls_that_unsupported_not_pass(self):
        """Exit 4 is "cannot answer" and does not close a claim. It was 0."""
        root = self._repo()
        sub = root / "subject.json"
        sub.write_text(json.dumps(
            {"repo_root": str(root),
             "subject_refs": [{"kind": "file", "path": "app/h.go"}]}))
        env = dict(os.environ, PATH="")          # no `go`: shape returns None
        r = subprocess.run(
            [sys.executable, str(ROOT / "checkers" / "dangling_ref.py"),
             "--subject", str(sub)],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("app/h.go", r.stdout)
        self.assertNotIn("every same-repo reference resolves", r.stdout)

    def test_and_a_tree_it_could_read_still_answers(self):
        """The check is not weakened: a readable tree still gets a verdict."""
        root = self._repo(ignored=False)         # compiler reads it: skipped
        found, unread = dangling_ref.go_scan(root, ["app/h.go"])
        self.assertEqual((found, unread), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
