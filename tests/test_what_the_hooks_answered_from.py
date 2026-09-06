"""Four places a hook picked its own answer.

    python3 -m unittest tests.test_what_the_hooks_answered_from -v

A census that printed the strongest green from an empty set; a program name
taken from a shell assignment and written into an append-only table; a list of
what ends a task, defaulted to a second copy in the function whose docstring
says not to; and the same bootstrap question answered three times in three
files. All four reported by an adopter; all four reproduced here.

All of them fail against 1dda490.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import doctor  # noqa: E402
from kernel import ledger as ledger_mod  # noqa: E402


def _hook(name):
    spec = importlib.util.spec_from_file_location(
        f"v4_hook_probe_{name}", ROOT / "hooks" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bash_guard = _hook("bash_guard")
stop_gate = _hook("stop_gate")
write_block = _hook("write_block")
framework = _hook("_framework")


def _repo(case, *, hooks=True):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    if hooks:
        (tmp / "hooks").mkdir()
        for h in ("bash_guard.py", "write_block.py", "stop_gate.py"):
            (tmp / "hooks" / h).write_text("# hook\n", encoding="utf-8")
        (tmp / ".claude").mkdir()
        (tmp / ".claude" / "settings.json").write_text(json.dumps(
            {"hooks": ["hooks/bash_guard.py", "hooks/write_block.py",
                       "hooks/stop_gate.py"]}))
    return tmp


class ACensusWithNoEvidenceSaysSo(unittest.TestCase):
    """The comment says a history older than the `hook` field is "reported as
    none of them named itself rather than as three dead hooks", and then an
    empty `seen` made `silent` empty and fell through to `each has fired`.
    Measured by an adopter: 1,823 rows, every `payload.hook` null, and
    `ok  3 hook(s) wired, each has fired`."""

    def _row(self, marks):
        tmp = _repo(self)
        conn = ledger_mod.connect(tmp)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        for payload in marks:
            ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                              kind="hook_seen", actor="hook", payload=payload,
                              created_at="2026-09-05T00:00:00+00:00")
        conn.close()
        out = []
        doctor._check_hooks_are_called_and_not_merely_present(tmp, out)
        return json.dumps(out[0], default=str)

    def test_rows_that_predate_the_field_are_not_read_as_all_fired(self):
        got = self._row([{"path": "x.py", "allowed": True}] * 3)
        self.assertNotIn("each has fired", got)
        self.assertIn("none of them named itself", got)

    def test_and_three_that_named_themselves_are_clean(self):
        """The control. A row that never says `each has fired` says nothing."""
        got = self._row([{"path": "x.py", "allowed": True, "hook": h}
                         for h in ("bash_guard", "write_block", "stop_gate")])
        self.assertIn("each has fired", got)

    def test_and_a_silent_hook_is_still_named(self):
        """The case that already worked, and must keep working: some named
        themselves and one did not."""
        got = self._row([{"path": "x.py", "allowed": True, "hook": h}
                         for h in ("bash_guard", "write_block")])
        self.assertIn("stop_gate", got)
        self.assertIn("never left a mark", got)


class TheProgramNameIsNotAnAssignment(unittest.TestCase):
    """`cmd.split()[0]` is the program only when the shell was not handed
    assignments first. An adopter's committed export already carries nine
    `hook_seen` rows whose program name is an assignment."""

    def test_an_assignment_prefix_is_not_the_program(self):
        # Synthetic: never issued, never valid. The point is the shape of the
        # prefix, not the value.
        pw = "hunter" + "2"
        self.assertEqual(
            bash_guard._program_name(f"PGPASSWORD={pw} psql -c 'select 1'"),
            "psql")

    def test_and_several_of_them_are_not_either(self):
        self.assertEqual(
            bash_guard._program_name("V4_HOME=/x PYTHONPATH=/y ./bin/v4 status"),
            "./bin/v4")

    def test_an_ordinary_command_is_unchanged(self):
        """The control. Stripping too eagerly would lose the program."""
        self.assertEqual(bash_guard._program_name("ls -la"), "ls")
        self.assertEqual(bash_guard._program_name("git status .v4"), "git")

    def test_a_command_that_is_only_assignments_says_that(self):
        """There is no program, and reporting the last assignment as one is the
        defect this repairs, one word over."""
        self.assertEqual(bash_guard._program_name("FOO=1"), "(assignments only)")
        self.assertEqual(bash_guard._program_name(""), "(no command)")

    def test_and_the_row_the_hook_writes_carries_the_program(self):
        """Entered rather than read: the value that lands in the append-only
        table is what this is about."""
        tmp = _repo(self)
        conn = ledger_mod.connect(tmp)
        ledger_mod.insert(conn, "task", id="t-live", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        conn.close()
        pw = "hunter" + "2"
        with redirect_stderr(io.StringIO()):
            bash_guard._mark(tmp, f"PGPASSWORD={pw} psql -c x", allowed=True,
                             reason="", basis="test", session="s")
        conn = sqlite3.connect(
            f"file:{tmp / '.git/v4/ledger.db'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = [json.loads(r["payload"]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'hook_seen'")]
        conn.close()
        self.assertTrue(rows, "no mark was written")
        self.assertEqual(rows[0]["path"], "psql")
        self.assertNotIn(pw, json.dumps(rows))


class WhatEndsATaskHasOneOwner(unittest.TestCase):
    """`getattr(ledger, "ENDED_KINDS", ("shipped", "abandoned"))` -- a second
    copy, in the function whose docstring says asking the owner "keeps this from
    being a fourth copy that drifts"."""

    def test_a_third_ending_reaches_the_hook(self):
        tmp = _repo(self)
        conn = ledger_mod.connect(tmp)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        ledger_mod.insert(conn, "event", task_id="t", claim_id=None,
                          kind="blocked", actor="kernel", payload={},
                          created_at="2026")
        conn.close()
        was = ledger_mod.ENDED_KINDS
        ledger_mod.ENDED_KINDS = ("shipped", "abandoned", "blocked")
        try:
            self.assertTrue(stop_gate._ended(tmp, "t"),
                            "the hook fell back to its own list")
        finally:
            ledger_mod.ENDED_KINDS = was

    def test_and_a_task_with_no_ending_is_still_open(self):
        """The control. Answering `True` for everything would pass the case
        above and let every turn stop."""
        tmp = _repo(self)
        conn = ledger_mod.connect(tmp)
        ledger_mod.insert(conn, "task", id="t", request="r" * 80,
                          scope_globs=["**"], base_commit="x",
                          created_at="2026")
        conn.close()
        self.assertFalse(stop_gate._ended(tmp, "t"))


class TheSessionsTaskHasOneOwner(unittest.TestCase):
    """Three files read `V4_TASK` and answered a non-trivial rule three ways.
    Both record the same incident: a shell exporting a task that had been
    abandoned, and every write in the new task judged against the old scope."""

    def _with(self, tmp, value):
        old = dict(os.environ)
        os.environ["V4_REPO"] = str(tmp)
        os.environ["V4_TASK"] = value
        try:
            return framework.task_id(tmp)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def _repo_with_an_ended_task(self):
        tmp = _repo(self)
        conn = ledger_mod.connect(tmp)
        for tid in ("t-over", "t-live"):
            ledger_mod.insert(conn, "task", id=tid, request="r" * 80,
                              scope_globs=["**"], base_commit="x",
                              created_at="2026")
        ledger_mod.insert(conn, "event", task_id="t-over", claim_id=None,
                          kind="abandoned", actor="person",
                          payload={"why": "w" * 50}, created_at="2026")
        conn.close()
        return tmp

    def test_a_variable_naming_an_ended_task_is_dropped_and_handed_back(self):
        tmp = self._repo_with_an_ended_task()
        self.assertEqual(self._with(tmp, "t-over"), ("", "t-over"))

    def test_and_a_live_one_is_taken(self):
        """The control: dropping every value would pass the case above."""
        tmp = self._repo_with_an_ended_task()
        self.assertEqual(self._with(tmp, "t-live"), ("t-live", ""))

    def test_and_no_variable_is_neither(self):
        tmp = self._repo_with_an_ended_task()
        old = dict(os.environ)
        os.environ.pop("V4_TASK", None)
        try:
            self.assertEqual(framework.task_id(tmp), ("", ""))
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_both_hooks_reach_the_same_answer(self):
        """The invariant the three copies were free to break: the same
        environment, the same repo, the same answer."""
        tmp = self._repo_with_an_ended_task()
        old = dict(os.environ)
        os.environ["V4_REPO"], os.environ["V4_TASK"] = str(tmp), "t-over"
        try:
            with redirect_stderr(io.StringIO()):
                _root, gate_task, gate_stale = stop_gate._repo_and_task()
            from_framework = framework.task_id(tmp)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(gate_stale, from_framework[1])
        self.assertNotEqual(gate_task, "t-over")


if __name__ == "__main__":
    unittest.main()
