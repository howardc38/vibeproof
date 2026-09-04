"""The last of the 2026-08-18 sweep: one matcher, one manifest, one contract.

    python3 -m unittest tests.test_the_last_of_the_sweep -v

What is pinned here is mostly *absence*: a path that is no longer exempt by
name, a file that is no longer missing from the manifest, a refusal that is no
longer sometimes a return value, and a gate that no test had ever run.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import (config, hashing, init as init_mod, install, ledger,  # noqa: E402
                    lifecycle, redgreen, request_cover)
from kernel.analysis import subject_files  # noqa: E402


def _repo(case, files=None):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, capture_output=True)
    for rel, text in (files or {}).items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp


class OneMatcherOrTwoAnswers(unittest.TestCase):
    """`excluded` accepted four spellings and `_files_in_scope` three.

    Measured on this repo's own `derive_exclude` (`**/__pycache__/**`): for a
    root-level `__pycache__/x.pyc` the first said excluded and the second said
    not, so derive scanned the file and every checker's fallback skipped it --
    which is the sentence `excluded`'s own docstring uses to say why that must
    not happen.
    """

    CASES = [("__pycache__/x.pyc", ["**/__pycache__/**"]),
             ("pkg/__pycache__/x.pyc", ["**/__pycache__/**"]),
             ("requirements.txt", ["**/requirements*.txt"]),
             ("x.py", ["**/*.py"]),
             ("tests/fixtures/red/a.py", ["tests/fixtures/**"])]

    def test_both_halves_of_the_system_answer_the_same(self):
        root = _repo(self, {rel: "x = 1\n" for rel, _ in self.CASES})
        subprocess.run(["git", "add", "-A", "-f"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "in"], cwd=root, capture_output=True)
        for rel, globs in self.CASES:
            with self.subTest(rel=rel):
                mine = subject_files.excluded(rel, globs)
                theirs = rel in lifecycle._files_in_scope(root, globs)
                self.assertEqual(mine, theirs, (rel, globs))


class ExemptByNameIsNotTheSameAsUnedited(unittest.TestCase):
    """`.v4/checkers.json` was exempt by name, whatever its content.

    That file maps each claim kind to the program that judges it, and it lives
    under `.v4/**` -- the path the scope checker exists to protect -- so a hand
    edit repointing a kind at a more permissive checker was never reported.
    """

    def test_the_registry_is_not_exempt(self):
        self.assertFalse(hashing.kernel_written(".v4/checkers.json"))

    def test_and_what_is_exempt_is_what_no_hand_writes(self):
        self.assertTrue(hashing.kernel_written(".v4/chain_head.json"))
        self.assertTrue(hashing.kernel_written(".v4/installed.json"))
        self.assertTrue(hashing.kernel_written(".gitignore"))


class WhatTheInstallerWroteAndWhatItSaid(unittest.TestCase):
    """`write_launcher` appends a block to the adopter's `.gitignore` and
    nothing recorded that it had, so the next task saw it in `git diff` with
    nobody able to explain it."""

    def test_the_gitignore_it_writes_is_accounted_for(self):
        dst = _repo(self)
        (dst / ".v4").mkdir()
        install.write_launcher(dst, ROOT)
        self.assertTrue((dst / ".gitignore").is_file())
        self.assertTrue(hashing.kernel_written(".gitignore"),
                        "written by the installer and answerable for")


class ARepoTemplateThatOnlySaysWhatThisRepoDecided(unittest.TestCase):
    """`CONFIG_TEMPLATE` wrote a second copy of the framework's protected set
    into every adopter, and `protected_for` unions rather than reads -- so the
    line was one an adopter could delete with no effect at all."""

    def test_the_scaffolded_config_states_no_framework_defaults(self):
        root = _repo(self)
        init_mod.scaffold(root)
        cfg = json.loads((root / ".v4" / "config.json").read_text())
        self.assertEqual(cfg["protected_paths"], [])
        got = config.RepoConfig(root)
        for glob in subject_files.PROTECTED_DEFAULT:
            self.assertIn(glob, got.protected,
                          "deleted from the file and still in force")


class OneContractForOneRefusal(unittest.TestCase):
    """`withdraw` raised for two failures and returned a string for the third,
    so a caller had to handle both -- and the checker that reaches the same
    rules through `measure`/`fault` saw neither."""

    def _conn(self, root):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="make the gate report what "
                      "it measured", scope_globs=["**"], base_commit="",
                      created_at="2026-08-19T00:00:00+00:00")
        return conn

    def test_every_refusal_is_the_same_shape(self):
        root = _repo(self)
        conn = self._conn(root)
        request = "make the gate report what it measured"
        request_cover.record(conn, task_id="t1", request=request,
                             quote="what it measured", not_done=True,
                             why="a reason long enough to pass the floor here",
                             min_chars=10, root=root)
        for quote, why in (("", "long enough to pass the floor here"),
                           ("not in the request", "long enough to pass here"),
                           ("what it measured", "short")):
            with self.subTest(quote=quote):
                with self.assertRaises(request_cover.NotInTheRequest):
                    request_cover.withdraw(conn, task_id="t1", quote=quote,
                                           why=why, min_chars=20)


class TheGateThatDecidesWhetherAFindingMayClose(unittest.TestCase):
    """Never executed by the suite: `raise AssertionError` as the first
    statement of `verify()` left 772 tests green.

    This is the function that decides whether any review finding may close --
    red at the parent, green at HEAD, the symbol actually entered -- and the
    only thing that ran it was `v4 check` on a real claim.
    """

    def _repo_with_history(self):
        root = _repo(self, {"pkg/__init__.py": "",
                            "pkg/thing.py": "def add(a, b):\n    return a - b\n",
                            "tests/__init__.py": "",
                            "tests/test_thing.py":
                                "import unittest\n\nfrom pkg.thing import add\n\n\n"
                                "class T(unittest.TestCase):\n"
                                "    def test_add(self):\n"
                                "        self.assertEqual(add(2, 2), 4)\n"})
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "before"], cwd=root,
                       capture_output=True)
        parent = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True).stdout.strip()
        (root / "pkg" / "thing.py").write_text("def add(a, b):\n    return a + b\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "after"], cwd=root,
                       capture_output=True)
        return root, parent

    def test_a_test_that_fails_before_and_passes_after_earns_the_close(self):
        root, parent = self._repo_with_history()
        res = redgreen.verify(
            root, command=[sys.executable, "-m", "unittest",
                           "tests.test_thing"],
            test_path="tests/test_thing.py", target_file="pkg/thing.py",
            target_symbol="add", parent_commit=parent)
        self.assertTrue(res.ok, res.as_dict())
        self.assertTrue(res.symbol_executed)

    def test_a_test_that_already_passed_at_the_parent_does_not(self):
        root, parent = self._repo_with_history()
        (root / "tests" / "test_thing.py").write_text(
            "import unittest\n\nfrom pkg.thing import add\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertIsNotNone(add(2, 2))\n")
        res = redgreen.verify(
            root, command=[sys.executable, "-m", "unittest", "tests.test_thing"],
            test_path="tests/test_thing.py", target_file="pkg/thing.py",
            target_symbol="add", parent_commit=parent)
        self.assertFalse(res.ok)
        self.assertTrue([n for n in res.notes if "0ad6b61" in n or "passes" in n],
                        res.notes)

    def test_a_test_that_reads_the_source_instead_of_running_it_does_not(self):
        root, parent = self._repo_with_history()
        (root / "tests" / "test_thing.py").write_text(
            "import pathlib\nimport unittest\n\nfrom pkg.thing import add\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        add(2, 2)\n"
            "        self.assertIn('a + b',\n"
            "                      pathlib.Path('pkg/thing.py').read_text())\n")
        res = redgreen.verify(
            root, command=[sys.executable, "-m", "unittest", "tests.test_thing"],
            test_path="tests/test_thing.py", target_file="pkg/thing.py",
            target_symbol="add", parent_commit=parent)
        self.assertFalse(res.ok)
        self.assertTrue([n for n in res.notes if "source text" in n], res.notes)

    def test_a_check_that_could_not_run_says_so_rather_than_reporting_clean(self):
        """The bypass check was wrapped in `except Exception: pass`, so a test
        file it could not parse left `symbol_executed` at its tracer value and
        nothing in the notes -- indistinguishable from one that was examined."""
        root, parent = self._repo_with_history()
        (root / "tests" / "unreadable.py").write_text("def (:\n")
        res = redgreen.verify(
            root, command=[sys.executable, "-m", "unittest", "tests.test_thing"],
            test_path="tests/unreadable.py", target_file="pkg/thing.py",
            target_symbol="add", parent_commit=parent)
        self.assertTrue([n for n in res.notes if "did not run" in n], res.notes)


class TheFixturesSurviveAClone(unittest.TestCase):
    """The gate's verdict was a fact about one laptop.

    `tests/fixtures/scope/{green,red}/*` were gitlink entries with no
    `.gitmodules`, so a clone got ten empty directories, and every green
    fixture of `registry-consistency` depended on an empty directory git
    cannot store.
    """

    def test_the_two_sets_that_did_not_survive_one_pass_the_gate(self):
        """`scope` and `registry-consistency` are the two that failed in a
        clone: ten empty directories where the cases should be, and eight
        green cases resting on a directory git cannot store."""
        from kernel import register
        registry = json.loads((ROOT / ".v4" / "checkers.json").read_text())
        for kind in ("scope", "registry-consistency"):
            entry = registry[kind]
            with self.subTest(checker=kind):
                ok, report = register.verify_checker(
                    repo_root=ROOT, checker_path=ROOT / entry["path"],
                    fixtures_dir=ROOT / entry["fixtures"], kind=kind)
                self.assertTrue(ok, "\n".join(report["failures"]))

    def test_no_fixture_is_a_gitlink(self):
        out = subprocess.run(["git", "ls-files", "-s", "tests/fixtures"],
                             cwd=ROOT, capture_output=True, text=True).stdout
        gitlinks = [l for l in out.splitlines() if l.startswith("160000")]
        self.assertEqual(gitlinks, [])

    def test_every_fixture_case_git_can_store_has_a_file_in_it(self):
        tracked = subprocess.run(["git", "ls-files", "tests/fixtures"],
                                 cwd=ROOT, capture_output=True, text=True).stdout
        held = {Path(p).parent for p in tracked.splitlines()}
        for colour_dir in sorted((ROOT / "tests" / "fixtures").glob("*/*")):
            if not colour_dir.is_dir() or colour_dir.name.startswith("."):
                continue
            for case in sorted(colour_dir.iterdir()):
                if case.is_dir():
                    rel = case.relative_to(ROOT)
                    self.assertTrue(
                        any(str(h).startswith(str(rel)) for h in held),
                        f"{rel} holds nothing git can store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
