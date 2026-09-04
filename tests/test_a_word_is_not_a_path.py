"""Three mechanisms that could not say what they were unable to see.

    python3 -m unittest tests.test_a_word_is_not_a_path -v

Each was reported from an adopter running nine lanes against this framework, and
each has the same shape: something answered, the answer was wrong or unreachable,
and nothing in the answer said so.

  * the bash guard refused a `git commit -m` for the English words in the
    message, because a word the tokenizer had already isolated was carved into
    six candidates and `checkers` matched `checkers/**`
  * `v4 doctor` printed `ok facts` while four dead globs in the same table had
    `derive` refusing three detectors on every run
  * `v4 derive` advised `--emit-baseline`, which three checkers accept and no
    `v4` subcommand could pass

All three fail against the commit before this file.
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

from kernel import derive as derive_mod                         # noqa: E402
from kernel import doctor as doctor_mod                         # noqa: E402
from kernel.analysis import shell_command as sc                 # noqa: E402

PROTECTED = [".github/**", ".v4/**", "checkers/**", "detectors/**"]


class AWordIsNotAPath(unittest.TestCase):
    """`writes_to_protected` has three answers and the middle one is `None` --
    "a protected path is named and this cannot say it is only read". The
    question this fixes is what counts as naming one."""

    def verdict(self, cmd):
        return sc.writes_to_protected(cmd, PROTECTED)

    def test_a_commit_message_that_only_says_the_words(self):
        self.assertEqual(
            self.verdict("git commit -m 'rework the checkers and detectors story'"),
            [], "prose is not a path")

    def test_but_a_commit_message_quoting_a_real_path_is_still_refused(self):
        """The other half. Without it this is not a narrowing, it is an off
        switch: `git commit` is on no roll call, so a real path inside its
        argv is exactly the case the third answer exists for."""
        self.assertIsNone(
            self.verdict("git commit -m 'touch checkers/scope.py in passing'"))

    def test_a_path_buried_in_a_quoted_sub_command_is_still_found(self):
        """The case `_spellings` was written for, and the one a whitespace
        split cannot be dropped without losing."""
        self.assertIsNone(self.verdict('bash -c "rm .v4/config.json"'))
        self.assertIsNone(
            self.verdict("""python3 -c "open('.v4/config.json','w')" """))

    def test_a_bare_word_that_is_the_whole_argument_still_counts(self):
        """`rm checkers` names the directory. Nothing is carved here, so
        nothing changes -- the rule is about chunks lifted out of a word."""
        self.assertTrue(self.verdict("rm -rf checkers"))

    def test_an_option_carrying_a_path_still_counts(self):
        self.assertIsNone(self.verdict("git commit --reference=.v4/config.json"))

    def test_and_the_reads_still_read(self):
        self.assertEqual(self.verdict("grep -n x checkers/scope.py"), [])
        self.assertEqual(self.verdict("cat checkers/scope.py | sed -n 1,20p"), [])

    def test_a_real_write_is_still_named_with_its_file(self):
        hits = self.verdict("sed -i '' s/a/b/ checkers/scope.py")
        self.assertEqual([h[0] for h in hits], ["checkers/scope.py"])

    def test_what_this_gives_up_written_as_an_assertion(self):
        """A protected glob with no separator of its own, inside a quoted
        sub-command. Neither this repo nor the reference adopter has one -- all
        eight globs in the two of them are `something/**` -- and a miss stated
        as an executable assertion is a miss somebody can find."""
        self.assertEqual(sc.writes_to_protected('bash -c "rm Makefile"',
                                                ["Makefile"]), [])
        self.assertTrue(sc.writes_to_protected("rm Makefile", ["Makefile"]),
                        "unquoted, it is still caught")


def _repo(case, files):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "add", "-A"],
              ["git", "commit", "-qm", "base"]):
        subprocess.run(a, cwd=tmp, capture_output=True)
    return tmp


class AGlobThatMatchesNothingTurnsADetectorOff(unittest.TestCase):
    """`derive` refuses every facts-reading detector while one of these stands,
    and `doctor` -- whose stated job is telling wired from looks-wired -- read
    `seen_at` and stopped."""

    def test_a_dead_glob_is_found(self):
        root = _repo(self, {"app.py": "x = 1\n"})
        dead = derive_mod._filters_matching_nothing(
            root, {"ui_globs": ["web/ui/src/**"], "entrypoint_globs": ["app.py"]})
        self.assertEqual(dead, ["ui_globs: web/ui/src/**"])

    def test_a_live_glob_is_not(self):
        root = _repo(self, {"app.py": "x = 1\n"})
        self.assertEqual(derive_mod._filters_matching_nothing(
            root, {"entrypoint_globs": ["app.py"]}), [])

    def test_doctor_reports_it(self):
        """The row `doctor` did not have. `ok facts … every row still cites
        something` was true and was not the question."""
        root = _repo(self, {"app.py": "x = 1\n"})
        rows = self._facts_rows(root, {"ui_globs": ["web/ui/src/**"]})
        said = [r for r in rows if r.get("what") == "facts globs"]
        self.assertEqual(len(said), 1, rows)
        self.assertEqual(said[0]["status"], doctor_mod.BAD, said)
        self.assertIn("ui_globs: web/ui/src/**", said[0]["detail"])

    def _facts_rows(self, root, extra):
        """The doctor rows for a table carrying `extra`, via the real check.

        Named `facts.t.json` because the table declares `repo: t` and
        `layout.repo_name` prefers a repo's own declaration over its directory
        name -- getting that wrong here produced a report about the filename
        instead of about the globs, which is the whole point of the rule.
        """
        table = {
            "repo": "t", "generated_from_commit": "0" * 40,
            "outbound_write": [{"pattern": ".write_text", "kind": "fs",
                                "seen_at": "app.py:1"}],
            "auth_decision": [{"pattern": "require_user", "kind": "authz",
                               "seen_at": "app.py:1"}],
            "outbound_read": [{"pattern": ".read_text", "kind": "fs",
                               "seen_at": "app.py:1"}],
            "config_files": [], "protected_paths": [],
            "entrypoint_globs": ["app.py"], "ui_globs": [],
            # `validate` refuses an empty table without a stated absence, and
            # it is right to: an empty one makes every detector report a clean
            # repo. `protected_paths` is one of the five it can be declared for;
            # `config_files` is not, because it may be empty without saying so.
            "absent": {"protected_paths": "a fixture repo with one file"},
        }
        table.update(extra)
        (root / ".v4").mkdir(exist_ok=True)
        (root / ".v4/facts.t.json").write_text(json.dumps(table),
                                               encoding="utf-8")
        rows = []
        doctor_mod._check_facts_load_under_the_name_the_loader_prefers(root, rows)
        return rows

    def test_and_a_table_whose_globs_all_live_says_so(self):
        root = _repo(self, {"app.py": "x = 1\n"})
        rows = self._facts_rows(root, {"entrypoint_globs": ["app.py"]})
        said = [r for r in rows if r.get("what") == "facts globs"]
        self.assertEqual(len(said), 1, rows)
        self.assertIn("matches something", said[0]["detail"])

    def test_an_unreadable_tracked_list_is_not_a_clean_table(self):
        """`[]` from the helper reads as "no dead globs", which is the same
        answer a clean table gives. A directory that is not a repo cannot say
        the globs are fine."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        dead = derive_mod._filters_matching_nothing(tmp, {"ui_globs": ["a/**"]})
        self.assertTrue(dead)
        self.assertTrue(dead[0].startswith(derive_mod._UNREADABLE_PREFIX), dead)


class TheFlagTheAdviceNames(unittest.TestCase):
    """`v4 derive` prints "a delta kind takes `--emit-baseline`"."""

    def test_the_cli_accepts_it(self):
        from kernel import cli
        with self.assertRaises(SystemExit):
            cli.main(["run-checker", "--help"])

    def test_and_it_reaches_the_checker_argv(self):
        """Accepting a flag and passing it on are two things, and a flag that
        stops at the parser is the same as no flag."""
        seen = {}

        def fake_run(argv, repo_root, timeout_sec, td):
            seen["argv"] = list(argv)
            return 0, "", "", []

        from kernel import runner
        real = runner._run_contained
        runner._run_contained = fake_run
        self.addCleanup(setattr, runner, "_run_contained", real)
        root = _repo(self, {"c.py": "print(1)\n"})
        runner.run_checker(repo_root=root, checker_path=root / "c.py",
                           registered_sha=None, subject_payload={},
                           subject_refs=[], emit_baseline=True)
        self.assertIn("--emit-baseline", seen["argv"])

    def test_and_stays_off_by_default(self):
        seen = {}

        def fake_run(argv, repo_root, timeout_sec, td):
            seen["argv"] = list(argv)
            return 0, "", "", []

        from kernel import runner
        real = runner._run_contained
        runner._run_contained = fake_run
        self.addCleanup(setattr, runner, "_run_contained", real)
        root = _repo(self, {"c.py": "print(1)\n"})
        runner.run_checker(repo_root=root, checker_path=root / "c.py",
                           registered_sha=None, subject_payload={},
                           subject_refs=[])
        self.assertNotIn("--emit-baseline", seen["argv"])

    def test_the_three_checkers_the_advice_is_about_take_it(self):
        """If none did, the advice would be wrong in the other direction and
        the repair would be to delete the sentence.

        Run rather than read. This asserted `"--emit-baseline" in src`, which is
        true of a checker that mentions the flag in a comment and rejects it, and
        `test-shape` reported it on the ship that introduced it. argparse exits 2
        for an unrecognised option, so what is asserted is the exit code from
        actually handing each checker the flag."""
        root = _repo(self, {"app.py": "x = 1\n"})
        subject = root / "subject.json"
        subject.write_text(json.dumps(
            {"repo_root": str(root), "subject_refs": [], "params": {}}))
        for name in ("structural_lint", "external_write", "fail_closed"):
            r = subprocess.run(
                [sys.executable, str(ROOT / "checkers" / f"{name}.py"),
                 "--subject", str(subject), "--emit-baseline"],
                capture_output=True, text=True, cwd=ROOT)
            self.assertNotEqual(
                r.returncode, 2,
                f"{name} rejected the flag `v4 derive` tells workers to use:\n"
                f"{r.stderr[:400]}")
            self.assertNotIn("unrecognized arguments", r.stderr, name)


if __name__ == "__main__":
    unittest.main()
