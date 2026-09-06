"""Who may write what, and whether a refusal leaves a trace.

    python3 -m unittest tests.test_who_may_write_what -v

Four findings, one question. A guard that says "allowed" when it cannot see the
write target; a permission boundary whose refusals left no record; a gate asking
glob-against-glob where its own sibling documents that answering wrongly; and
the file that installs the guards, unprotected while the guards themselves are
protected.

All of them fail against c1409f3.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import config as config_mod  # noqa: E402
from kernel import ledger as ledger_mod  # noqa: E402
from kernel import scope  # noqa: E402
from kernel.analysis.shell_command import writes_to_protected  # noqa: E402
from kernel.analysis.subject_files import matches  # noqa: E402

GUARDED = [".github/**", ".v4/**", "checkers/**", "detectors/**", "hooks/**"]


def _verdict(cmd, protected=GUARDED):
    """`deny`, `refused` or `allow` -- the three answers, kept apart.

    `None` is "cannot tell", which the guard turns into a refusal, and `[]` is
    "allowed". A test that reads both as falsy proves nothing: measured while
    writing this, the first probe printed ALLOW for a command the repair had
    just started refusing."""
    r = writes_to_protected(cmd, protected)
    if r is None:
        return "refused"
    return "deny" if r else "allow"


class AWriterThatNamesNoTarget(unittest.TestCase):
    """`patch`, `git apply` and `git stash pop` are on the roll call, and being
    on it sets `known`, which suppresses the cannot-tell branch. That trade is
    right for `rm`, whose branch walks every operand. These name nothing: the
    diff or the stash decides, and it can name `checkers/*.py` as easily as
    anything else."""

    def test_patch_from_stdin(self):
        self.assertEqual(_verdict("patch -p1 < /tmp/p.diff"), "refused")

    def test_patch_from_a_file(self):
        self.assertEqual(_verdict("patch -p1 -i /tmp/p.diff"), "refused")

    def test_git_apply(self):
        self.assertEqual(_verdict("git apply /tmp/p.diff"), "refused")
        self.assertEqual(_verdict("git apply --3way /tmp/p.diff"), "refused")

    def test_git_stash_pop(self):
        self.assertEqual(_verdict("git stash pop"), "refused")

    def test_and_bare_git_stash_too(self):
        """It takes the working tree away, protected files included, so the
        absence of a verb is not the absence of a write."""
        self.assertEqual(_verdict("git stash"), "refused")

    def test_but_git_stash_list_reads(self):
        """The narrowing. A targeted obligation that grew to cover the reading
        verbs would be the blanket this project's own rules refuse -- measured:
        the first spelling of this repair refused `git stash list`."""
        self.assertEqual(_verdict("git stash list"), "allow")
        self.assertEqual(_verdict("git stash show"), "allow")

    def test_and_a_writer_that_does_name_its_target_still_denies(self):
        """The control. Moving these into cannot-tell must not cost the
        answers the guard already gives."""
        self.assertEqual(_verdict("rm -rf checkers"), "deny")
        self.assertEqual(_verdict("rm -rf ."), "deny")

    def test_and_ordinary_commands_are_still_allowed(self):
        """The other control: a guard that refuses everything is one nobody
        keeps."""
        for cmd in ("ls -la", "git status .v4", "cat kernel/ledger.py",
                    "git log --oneline", "git diff --name-only"):
            with self.subTest(cmd):
                self.assertEqual(_verdict(cmd), "allow")


class _Widen(unittest.TestCase):
    def _repo(self, forbid=()):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                        "add", "-A"], cwd=tmp, capture_output=True)
        conn = ledger_mod.connect(tmp)
        self.addCleanup(conn.close)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["docs/**"], base_commit="x",
                          created_at="2026")
        if forbid:
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="task_forbid", actor="person",
                              payload={"globs": list(forbid)},
                              created_at="2026")
        cfg = config_mod.RepoConfig(tmp)
        return tmp, conn, cfg

    def _widens(self, conn):
        return [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'scope_widen' ORDER BY id")]


class EveryRefusalLeavesARow(_Widen):
    """Two of the three refusals raised before any insert ran, and `usage()`
    counts refusals off a flag on the row. So a widen turned down for reaching
    a forbidden path counted as zero attempts."""

    def test_a_reason_too_short_is_recorded(self):
        _root, conn, cfg = self._repo()
        with self.assertRaises(scope.WidenRefused):
            scope.widen(conn, cfg, task_id="t", add=["src/**"], why="no")
        rows = self._widens(conn)
        self.assertEqual(len(rows), 1, "the attempt left no row at all")
        self.assertIs(rows[0]["engagement"]["accepted"], False)

    def test_a_forbidden_path_is_recorded(self):
        """The permission-boundary event this table is for, and the one
        refusal that produced no evidence anybody tried."""
        _root, conn, cfg = self._repo(forbid=["src/**"])
        with self.assertRaises(scope.WidenRefused):
            scope.widen(conn, cfg, task_id="t", add=["src/**"],
                        why="This is a reason long enough to pass the floor "
                            "and it names what it wants and why.")
        rows = self._widens(conn)
        self.assertEqual(len(rows), 1, "the attempt left no row at all")
        self.assertIs(rows[0]["engagement"]["accepted"], False)

    def test_and_the_refusal_count_sees_them(self):
        """`usage()` is what reads that flag, so it is what gets asked."""
        _root, conn, cfg = self._repo(forbid=["src/**"])
        with self.assertRaises(scope.WidenRefused):
            scope.widen(conn, cfg, task_id="t", add=["src/**"],
                        why="This is a reason long enough to pass the floor "
                            "and it names what it wants and why.")
        used = scope.usage(conn, cfg, "t")
        self.assertGreaterEqual(used.get("refused", 0), 1, used)

    def test_and_a_widen_that_succeeds_is_not_counted_as_refused(self):
        """The control. A row written on every path with the flag always false
        would pass every case above."""
        _root, conn, cfg = self._repo()
        scope.widen(conn, cfg, task_id="t", add=["src/**"],
                    why="This is a reason long enough to pass the floor and "
                        "it names src/** and why this task needs it.")
        rows = self._widens(conn)
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["engagement"]["accepted"], True)
        self.assertEqual(scope.usage(conn, cfg, "t").get("refused", 0), 0)


class TheForbiddenGateAsksAboutPaths(_Widen):
    """It asked `_matches(glob, forbidden)` -- glob against glob -- which the
    sibling `_reaches_protected` documents as answering wrongly in the one
    direction that costs something, and was repaired there and left here."""

    def test_a_wide_glob_that_reaches_a_forbidden_file_is_refused(self):
        _root, conn, cfg = self._repo(forbid=["src/a.py"])
        with self.assertRaises(scope.WidenRefused):
            scope.widen(conn, cfg, task_id="t", add=["src/**"],
                        why="This is a reason long enough to pass the floor "
                            "and it names src/** and why this task needs it.")

    def test_and_a_glob_that_reaches_nothing_forbidden_still_passes(self):
        """The control: refusing every widen would satisfy the case above."""
        _root, conn, cfg = self._repo(forbid=["docs/secret.md"])
        globs, _hits = scope.widen(
            conn, cfg, task_id="t", add=["src/**"],
            why="This is a reason long enough to pass the floor and it names "
                "src/** and why this task needs it.")
        self.assertIn("src/**", globs)


class TheFileThatInstallsTheGuards(unittest.TestCase):
    """Weakening a checker costs a signature and a re-registration. Weakening
    the three hooks that make every write-time decision cost a scope widen
    sentence until `hooks/**` was protected -- and the file that installs them
    still did."""

    def test_the_settings_file_is_protected(self):
        prot = config_mod.RepoConfig(ROOT).protected
        self.assertTrue(matches(".claude/settings.json", prot))

    def test_and_the_hooks_themselves_still_are(self):
        prot = config_mod.RepoConfig(ROOT).protected
        for p in ("hooks/bash_guard.py", "hooks/write_block.py",
                  "hooks/stop_gate.py"):
            with self.subTest(p):
                self.assertTrue(matches(p, prot))

    def test_but_an_agent_role_is_not(self):
        """Narrow on purpose. `.claude/**` would put every role prompt behind a
        signature, and a role prompt is not an authorisation surface -- the
        settings file is."""
        prot = config_mod.RepoConfig(ROOT).protected
        self.assertFalse(matches(".claude/agents/reviewer.md", prot))


if __name__ == "__main__":
    unittest.main()
