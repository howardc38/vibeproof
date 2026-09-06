"""Five checks, and what each of them was answering from.

    python3 -m unittest tests.test_checks_that_answered_from_nothing -v

A hook census that read filenames out of a JSON file; a query computed on every
run and thrown away under a comment explaining what it decided; a cleanup that
could not report its own failure; and a coverage match that let one file answer
for another.

All of them fail against 655db9c.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import doctor, redgreen  # noqa: E402
from kernel import ledger as ledger_mod  # noqa: E402


class OneFileDoesNotAnswerForAnother(unittest.TestCase):
    """The Go and Node tracers asked `endswith`. It was repaired once, from the
    bare basename to the repo-relative path -- `internal/cache/store.go`
    answering a claim at `internal/db/store.go` -- and the residue is the same
    shape one directory out."""

    def test_a_path_that_ends_mid_segment_is_not_it(self):
        """The case a boundary rule can decide, and the one bare `endswith`
        got wrong: a target that is a bare filename at the repo root.

        I expected to be able to refuse `vendor/internal/db/store.go` for a
        claim at `internal/db/store.go` as well, and measured that it cannot
        be: Go reports `<module path>/<package-relative>` and V8 reports a
        `file://` URL, so a legitimate hit reads
        `github.com/x/y/internal/db/store.go` -- the same shape as the vendored
        one. Nothing in either format says which prefix is the module and which
        is another tree. The expectation was wrong as a requirement, not merely
        unmet, and refusing both would refuse every real Go hit."""
        self.assertFalse(redgreen._same_file("a_store.go", "store.go"))
        self.assertFalse(redgreen._same_file("xstore.go", "store.go"))

    def test_and_a_basename_collision_is_still_refused(self):
        """The repair that landed earlier: `want` is the repo-relative path,
        not the bare name, so a same-named file in another directory no longer
        answers."""
        self.assertFalse(redgreen._same_file(
            "internal/cache/store.go", "internal/db/store.go"))

    def test_but_the_file_itself_matches(self):
        """The control. A rule that matches nothing would pass both cases
        above and close no finding at all."""
        self.assertTrue(redgreen._same_file(
            "internal/db/store.go", "internal/db/store.go"))

    def test_and_so_does_a_prefix_that_stops_on_a_boundary(self):
        """Go reports package-relative paths and V8 reports `file://` URLs, so
        equality alone cannot be the rule."""
        self.assertTrue(redgreen._same_file(
            "github.com/x/y/internal/db/store.go", "internal/db/store.go"))
        self.assertTrue(redgreen._same_file(
            "file:///tmp/repo/internal/db/store.go", "internal/db/store.go"))

    def test_the_go_reader_uses_it(self):
        """Entered rather than read: a helper nothing calls is not a repair.
        Two covered files, one of them merely ending with the target."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        profile = tmp / "cover.out"
        profile.write_text("mode: set\n", encoding="utf-8")
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                [], 0,
                "ex/a_store.go:10:\tRun\t93.3%\n", "")
            got = redgreen._go_executed(tmp, profile, "store.go", "Run")
        self.assertEqual(got, (None, 0),
                         "`a_store.go` answered for a claim at `store.go`")

    def test_and_the_node_reader_uses_it(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "c.json").write_text(json.dumps({"result": [
            {"url": "file:///repo/a_store.js",
             "functions": [{"functionName": "run",
                            "ranges": [{"count": 3}]}]}]}), encoding="utf-8")
        got = redgreen._node_executed(tmp, "store.js", "run")
        self.assertEqual(got, (None, 0),
                         "`a_store.js` answered for a claim at `store.js`")


class ACleanupThatCanSayItFailed(unittest.TestCase):
    """Every other step in `verify` reports its own failure by name. The
    worktree removal ran with `capture_output=True` and neither the code nor
    the output was looked at, while `git worktree prune` appears nowhere in
    this tree -- so a leak had no second mechanism and left no line."""

    def test_a_removal_that_fails_is_reported(self):
        real = subprocess.run

        def fake(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd[:3] == ["git", "worktree",
                                                     "remove"]:
                return subprocess.CompletedProcess(
                    cmd, 1, "", "fatal: working trees containing submodules")
            return real(cmd, *a, **kw)

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / "a.py").write_text("def f():\n    return 1\n")
        (tmp / "t_a.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertTrue(True)\n")
        for args in (["add", "-A"], ["commit", "-qm", "base"]):
            subprocess.run(["git", "-c", "user.email=a@b", "-c",
                            "user.name=c", *args], cwd=tmp,
                           capture_output=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                              capture_output=True, text=True).stdout.strip()
        with mock.patch("kernel.redgreen.subprocess.run", side_effect=fake):
            res = redgreen.verify(
                tmp, command=[sys.executable, "-m", "unittest", "-q", "t_a"],
                test_path="t_a.py", target_file="a.py", target_symbol="f",
                parent_commit=head)
        self.assertTrue(
            any("worktree could not be removed" in n for n in res.notes),
            f"a leaked worktree said nothing: {res.notes}")


class ADeadLocalUnderALiveComment(unittest.TestCase):
    """A query that ran on every `v4 doctor` and was thrown away, with five
    lines above it explaining what it decided. The exclusion it describes is a
    subquery two lines down.

    The half that reads the source lives in
    `tests/test_two_modules_that_have_to_agree.py`: `redgreen.verify` parses
    this whole file, and one source assertion anywhere in it means the file can
    close no finding at all -- measured here, all five were refused."""

    def test_and_the_check_still_answers(self):
        """The behavioural half. Removing a local must not remove a verdict."""
        out = []
        doctor._check_a_registered_checker_that_has_never_executed(ROOT, out)
        self.assertTrue(out, "the check produced no row at all")


class EachHookIsAskedSeparately(unittest.TestCase):
    """`3 hook(s) wired` was counted by finding each filename as a substring of
    `.claude/settings.json`, and `last fired` read one arbitrary row. The Stop
    gate -- the only one that can block a turn -- recorded nothing at all, so it
    would have read `wired, last fired <now>` on the day it stopped running."""

    def _repo(self, marks=()):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / "hooks").mkdir()
        for h in ("bash_guard.py", "write_block.py", "stop_gate.py"):
            (tmp / "hooks" / h).write_text("# hook\n", encoding="utf-8")
        (tmp / ".claude").mkdir()
        (tmp / ".claude" / "settings.json").write_text(json.dumps(
            {"hooks": ["hooks/bash_guard.py", "hooks/write_block.py",
                       "hooks/stop_gate.py"]}))
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        conn = ledger_mod.connect(tmp)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        for name in marks:
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="hook_seen", actor="hook",
                              payload={"path": "x.py", "allowed": True,
                                       "hook": name},
                              created_at="2026-09-05T00:00:00+00:00")
        conn.close()
        return tmp

    def _row(self, root):
        out = []
        doctor._check_hooks_are_called_and_not_merely_present(root, out)
        return out[0]

    def test_a_hook_that_never_fired_is_named(self):
        root = self._repo(marks=("bash_guard", "write_block"))
        row = self._row(root)
        self.assertIn("stop_gate", json.dumps(row, default=str),
                      f"the silent hook was not named: {row}")

    def test_and_three_that_have_all_fired_are_clean(self):
        """The control. A row that always names somebody says nothing."""
        root = self._repo(marks=("bash_guard", "write_block", "stop_gate"))
        row = json.dumps(self._row(root), default=str)
        self.assertNotIn("never left a mark", row, row)

    def test_and_a_history_older_than_the_name_is_not_read_as_three_dead(self):
        """Every row written before the payload carried a hook name is not
        evidence about any hook. Reporting those repos as three dead hooks
        would be the gate going red on everyone at once."""
        root = self._repo()
        conn = ledger_mod.connect(root)
        ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                          kind="hook_seen", actor="hook",
                          payload={"path": "x.py", "allowed": True},
                          created_at="2026-09-05T00:00:00+00:00")
        conn.close()
        row = json.dumps(self._row(root), default=str)
        self.assertNotIn("never left a mark", row, row)


if __name__ == "__main__":
    unittest.main()
