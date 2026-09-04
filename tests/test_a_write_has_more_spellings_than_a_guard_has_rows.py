"""The shell guard stopped recognising writes and started recognising reads.

    python3 -m unittest tests.test_a_write_has_more_spellings_than_a_guard_has_rows

Four findings that are one fact: `hooks/bash_guard.py::main` (408aee58),
`kernel/analysis/shell_command.py::_protected` (5b535a6e), and
`::writes_to_protected` twice (c878bb32, c7b0d0d6). Not one of them was an idea
the roll call had missed -- each was a different *spelling* of a write already
on it, measured returning `[]` against this repo's own protected set while the
canonical spelling of the identical write was refused.

So the last class here is the one that matters. Four more rows would have left
the fifth spelling, and the repair is that the roll call which has to be
complete is now `READERS`: an omission there is a refusal somebody reads, where
an omission in `WRITERS` was silence.

All of these fail against 0785a00.
"""

from __future__ import annotations

import io
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
sys.path.insert(0, str(ROOT / "hooks"))

import bash_guard                                               # noqa: E402
from kernel import ledger                                       # noqa: E402
from kernel.analysis import shell_command                       # noqa: E402

#: This repo's own protected set, which is what all four were measured against.
P = [".v4/**", "checkers/**", "detectors/**", ".github/**"]


def _hits(cmd):
    return shell_command.writes_to_protected(cmd, P)


def _refused(cmd):
    """What the hook denies on: a write it can name, or `None`."""
    r = _hits(cmd)
    return r is None or bool(r)


class AnInterpreterIsNotJudgeableFromItsArgv(unittest.TestCase):
    """408aee58 -- `hooks/bash_guard.py::main`.

    Measured: `writes_to_protected` returned `[]` for a `python3 -c` and for a
    `node -e` that each open `.v4/config.json` for writing, so `main` took the
    `hits == []` branch and printed `{}` -- allow -- while `sed -i`, `tee` and
    `>` spellings of the identical write were all denied. The line parses and
    trips no `UNSUPPORTED` token, so the one branch that fails closed never
    applied to it.
    """

    PY = "python3 -c \"open('.v4/config.json','w').write('x')\""
    JS = "node -e \"require('fs').writeFileSync('.v4/config.json','x')\""

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["app/**"],
                      base_commit="", created_at="2026")
        return tmp

    def _run(self, cmd, root):
        old_in, old_out = sys.stdin, sys.stdout
        old_repo = os.environ.get("V4_REPO")
        os.environ["V4_REPO"] = str(root)
        sys.stdin = io.StringIO(json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": cmd}}))
        sys.stdout = io.StringIO()
        try:
            return bash_guard.main(), sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            if old_repo is None:
                os.environ.pop("V4_REPO", None)
            else:
                os.environ["V4_REPO"] = old_repo

    def test_the_hook_denies_a_one_liner_that_opens_the_test_oracle(self):
        root = self._repo()
        for cmd in (self.PY, self.JS):
            with self.subTest(cmd=cmd):
                code, out = self._run(cmd, root)
                self.assertEqual(code, 0, "a hook must never exit non-zero")
                self.assertIn("deny", out)

    def test_and_it_says_it_could_not_read_the_command(self):
        """`main` prints two refusals and the difference is load-bearing:
        "rewrite it plainly" is not advice a `python3 -c` can act on, so the
        way out has to be named -- put the path on a command that only reads."""
        root = self._repo()
        _code, out = self._run(self.PY, root)
        said = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("cannot say it only reads", said)

    def test_and_still_lets_an_interpreter_that_names_nothing_through(self):
        """The control: denying every Bash call would pass both tests above."""
        root = self._repo()
        code, out = self._run('python3 -c "print(1)"', root)
        self.assertEqual(code, 0)
        self.assertNotIn("deny", out)

    def test_a_runner_standing_in_front_of_a_write_is_the_same_fact(self):
        """`sudo`, `env`, `xargs` and `bash -c` were each allowed for exactly
        the reason `python3` was, and fixing only the interpreter would have
        been the same defect one row further out."""
        for cmd in ("sudo rm .v4/config.json",
                    "env sed -i s/a/b/ .v4/config.json",
                    'bash -c "rm .v4/config.json"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(_refused(cmd), cmd)


class TheSameFileSpelledFromSomewhereElse(unittest.TestCase):
    """5b535a6e -- `kernel/analysis/shell_command.py::_protected`.

    It handed a raw argv word to `subject_files.matches`, whose own docstring
    says it takes a repo-relative path. Measured: `rm .v4/config.json` refused,
    `rm /path/to/repo/.v4/config.json` returned `[]` --
    the same file, the same command, one of them spelled from the root.
    """

    # Derived from this checkout, not from the machine it was written on. The
    # literal above is the command as it was measured; this is the same file
    # wherever the repo now sits, which is two bugs at once -- the name changed,
    # and a path under one person's home directory was never going to hold in
    # anybody else's clone.
    ABS = str(ROOT / ".v4" / "config.json")

    def test_an_absolute_path_names_the_same_protected_file(self):
        self.assertTrue(shell_command._protected(self.ABS, P))
        self.assertTrue(_hits("rm " + self.ABS))

    def test_and_so_do_the_spellings_a_repo_root_would_not_have_answered(self):
        """Resolving against the root -- which `hooks/write_block.py` does, and
        which is why this looked like a one-line repair -- answers the absolute
        case only. `$PWD` is not expanded here, and a sibling worktree of this
        same repo resolves outside whichever root the hook was handed, which is
        where the finding was reproduced."""
        for word in ("$PWD/.v4/config.json", "../wt-other/.v4/config.json"):
            with self.subTest(word=word):
                self.assertTrue(shell_command._protected(word, P))

    def test_and_a_path_carried_inside_one_argument(self):
        self.assertTrue(shell_command._protected("open('.v4/config.json','w')", P))
        self.assertTrue(shell_command._protected("--reference=.v4/config.json", P))

    def test_and_a_path_that_is_not_there_is_still_not_protected(self):
        """The control. Every spelling only ever adds a refusal, so what has to
        hold is that it adds none where there is nothing to refuse."""
        for word in ("docs/SPEC.md", "/tmp/config.json", "src/checkers_report.py",
                     "detectors_of_a_sort.md"):
            with self.subTest(word=word):
                self.assertFalse(shell_command._protected(word, P))


class AGlobalOptionCarryingAValueAteTheSubcommand(unittest.TestCase):
    """c878bb32 -- `::writes_to_protected`.

    `SUBCOMMAND_WRITERS` says the first word writes nothing and the second
    decides, and the second was read as the first word not starting with `-`.
    Measured: `git rm .v4/config.json` refused, `git -C . rm .v4/config.json`
    returned `[]`, because `.` was read as the subcommand.
    """

    def test_the_subcommand_is_found_past_an_option_that_took_a_value(self):
        for cmd in ("git -C . rm .v4/config.json",
                    "git -C /tmp/x checkout -- .v4/config.json",
                    "git --git-dir .git restore .v4/",
                    "git -c user.name=nobody rm checkers/scope.py"):
            with self.subTest(cmd=cmd):
                self.assertTrue(_refused(cmd), cmd)

    def test_and_reading_through_the_same_option_is_still_free(self):
        """Why `git` cannot simply be left unclassified: `git status .v4` is a
        read, and a guard that refuses those is the one that gets switched off."""
        for cmd in ("git status .v4", "git -C . status .v4",
                    "git log -- checkers/", "git -C . diff .v4"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_hits(cmd), [], cmd)

    def test_and_a_verb_on_neither_list_refuses_rather_than_allows(self):
        """What makes an incomplete option list survivable. An option this does
        not know shifts the subcommand onto a word that is on neither list, and
        that word is refused -- so the next missing row costs a sentence."""
        self.assertIsNone(_hits("git -C . frobnicate .v4/config.json"))


class OneFlagWithFourSpellings(unittest.TestCase):
    """c7b0d0d6 -- `::writes_to_protected`.

    `INPLACE` tested `arg.startswith("-i")`, which is one spelling of the flag.
    Measured on the same file in the same command: `perl -i -pe` and `sed -i`
    refused; `perl -pi -e`, `sed --in-place` and `sed -i.bak` returned `[]`.
    """

    def test_the_short_form_is_a_cluster_of_letters_not_a_prefix(self):
        for cmd in ("perl -pi -e s/a/b/ .v4/config.json",
                    "sed -i.bak s/a/b/ .v4/config.json",
                    "sed -ni s/a/b/ .v4/config.json",
                    "ruby -i.orig -pe 1 checkers/scope.py"):
            with self.subTest(cmd=cmd):
                self.assertTrue(_hits(cmd), cmd)

    def test_and_the_long_form_is_the_same_flag(self):
        self.assertTrue(_hits("sed --in-place s/a/b/ .v4/config.json"))

    def test_and_the_canonical_spellings_did_not_stop_being_writes(self):
        for cmd in ("sed -i s/a/b/ .v4/config.json",
                    "sed -i '' s/a/b/ .v4/config.json",
                    "perl -i -pe s/a/b/ .v4/config.json"):
            with self.subTest(cmd=cmd):
                self.assertTrue(_hits(cmd), cmd)

    def test_a_spelling_this_still_misses_is_refused_all_the_same(self):
        """The property that takes the flag out of the gate, and the reason
        this is not a fifth row. gawk's in-place is `--include inplace`, which
        `_in_place` reads as neither form -- and the command is refused anyway,
        because `awk` is not a reader. What the flag decides now is which
        sentence comes back, not whether one does."""
        self.assertFalse(shell_command._in_place(["--include", "inplace"]))
        self.assertIsNone(
            _hits("awk --include inplace -f /tmp/p.awk .v4/config.json"))


class TheRollCallIsNoLongerWhatRefuses(unittest.TestCase):
    """The repair, as a property rather than as four more rows.

    Every one of the four above was a spelling of a write already listed, and a
    further spelling is always available -- `sudo`, `xargs`, `env`, `unlink`
    and `ed` were sitting there untried. So the question a segment answers is
    no longer "is this a write" but "can this guard say it is only a read", and
    the roll call that has to be complete is the one whose incompleteness is
    loud.
    """

    def test_a_writer_nobody_listed_no_longer_walks_through(self):
        for cmd in ("unlink .v4/config.json", "ed .v4/config.json",
                    "xargs rm .v4/config.json", "busybox rm .v4/config.json"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(_hits(cmd), cmd)

    def test_and_neither_does_walking_into_the_directory_first(self):
        """`cd` is not a reader either, which is the only answer this guard has
        to a command whose working directory it cannot know."""
        self.assertIsNone(_hits("cd .v4"))

    def test_the_conjunction_that_keeps_it_from_refusing_everything(self):
        """A command on no list is ordinary until it names one of the four
        directories that judge the work. Without this the guard would refuse
        the repo's own test oracle."""
        for cmd in ("python3 tests/run_without_silent_skips.py",
                    "./bin/v4 --repo . check --task t-guard",
                    "for f in *; do echo $f; done",
                    "make build && npm test"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_hits(cmd), [], cmd)

    def test_and_reading_what_judges_the_work_stays_free(self):
        for cmd in ("cat .v4/config.json", "ls -la .v4/",
                    "head -50 checkers/scope.py", "wc -l detectors/scope.py",
                    'grep -n ">" .v4/config.json',
                    "cat .v4/config.json | sed -n 1,5p"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_hits(cmd), [], cmd)

    def test_the_guard_does_not_refuse_its_own_remedy(self):
        """Found by this repair rather than reported into it, and it would have
        shipped a guard that denies the command its own refusal prints.

        `v4` carries protected paths in its argv by design -- the escape the
        hook offers is `v4 scope widen --add <that protected path>`, a `--why`
        quotes the file it is about, and the widen that let this change land
        was itself one of these. Unclassified, every one of them is refused."""
        for cmd in ("v4 --repo . scope widen --task t "
                    "--add .v4/control_plane_budget.json --why 'because'",
                    "./bin/v4 --repo . engage --claim c "
                    "--text 'checkers/scope.py decides this'",
                    "./bin/v4 --repo . audit --events .v4/ledger_export.jsonl"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_hits(cmd), [], cmd)

    def test_but_the_one_v4_verb_that_writes_an_argv_path_is_still_a_write(self):
        """`v4` is not a reader, and the difference is one subcommand: `export
        --out <path>` writes the path it is handed. The global option carrying
        a value is the same trap `git -C` was, so it is read the same way."""
        self.assertTrue(
            _refused("./bin/v4 --repo . export --out .v4/ledger_export.jsonl"))

    def test_the_bill_this_repair_sends(self):
        """Stated rather than left to be discovered. `sed` on a protected path
        is refused even when it only reads, because calling `sed` a reader
        means trusting a flag spelling again -- the thing that failed four
        times above. The rewrite is on the line above this one."""
        self.assertIsNone(_hits("sed -n 1,20p checkers/scope.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
