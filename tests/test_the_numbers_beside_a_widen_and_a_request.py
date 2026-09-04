"""Two counters that overstated, and two keys nobody printed.

    python3 -m unittest tests.test_the_numbers_beside_a_widen_and_a_request -v

`scope.usage` had a comment saying that summing every `scope_widen` row was the
defect being repaired, above a line that then repaired only `added`: `widens`
stayed `len(rows)`, so a refused widen -- recorded, judged, and never applied --
was counted in the one number the design calls the entire defence against
sprawl. `refused` and `reached` were returned beside it and read by nothing.

`cli.cmd_cover` had the shape one layer up. `request_cover.measure` returns
`faults` (entries `fault()` threw out) and `unspoken` (clauses nobody quoted at
all), and `cmd_cover` printed neither -- so a rejected entry was listed as
delivered while the ratio above had already refused to count it, and the list
computed precisely because a character ratio hides a short clause had no reader
anywhere in the repo.

The two are one fact: a control surface reporting a number that reads better
than what it measured.
"""

from __future__ import annotations

import io
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, config, ledger, lifecycle, scope as scope_mod  # noqa: E402
from kernel import request_cover  # noqa: E402


def _repo(test) -> tuple:
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp, check=True, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk",
         "thresholds": {"min_chars": 40, "dup_threshold": 0.8,
                        "widen_warn_pct": 5}}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / "app").mkdir()
    (tmp / "app" / "x.py").write_text("x = 1\n")
    (tmp / "docs").mkdir()
    (tmp / "docs" / "NOTES.md").write_text("notes\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True,
                   capture_output=True)
    conn = ledger.connect(tmp)
    test.addCleanup(conn.close)
    return conn, config.RepoConfig(tmp), tmp


GOOD = ("docs/NOTES.md carries the note this task's repair is described in, "
        "so leaving it out would ship a description of a state that is gone")


class ARefusedWidenIsNotAWiden(unittest.TestCase):
    def setUp(self):
        self.conn, self.cfg, self.root = _repo(self)
        lifecycle.open_task(self.conn, self.cfg, task_id="t-w", request="r",
                            scope_globs=["app/**"])

    #: Long enough to pass the floor and about nothing that was added, so the
    #: refusal comes from `judge_text` -- which is the path that writes the
    #: event first and raises after, and therefore the only one `usage` can
    #: miscount.
    OFF_SUBJECT = ("the sender needs a wider reach than the task opened with "
                   "and this sentence explains it at length without ever "
                   "saying which path")

    def _refuse(self):
        with self.assertRaises(scope_mod.WidenRefused):
            scope_mod.widen(self.conn, self.cfg, task_id="t-w",
                            add=["docs/NOTES.md"], why=self.OFF_SUBJECT)

    def test_a_refusal_alone_counts_as_nothing(self):
        self._refuse()
        u = scope_mod.usage(self.conn, self.cfg, "t-w")
        self.assertEqual(0, u["widens"])
        self.assertEqual(1, u["refused"])
        self.assertEqual([], u["paths"])

    def test_an_accepted_one_counts_once_beside_it(self):
        self._refuse()
        scope_mod.widen(self.conn, self.cfg, task_id="t-w",
                        add=["docs/NOTES.md"], why=GOOD)
        u = scope_mod.usage(self.conn, self.cfg, "t-w")
        self.assertEqual(1, u["widens"],
                         "the refused row must not be in the widen count")
        self.assertEqual(1, u["refused"])

    def test_the_refusal_is_printed_where_the_count_is(self):
        self._refuse()
        u = scope_mod.usage(self.conn, self.cfg, "t-w")
        self.assertIn("refused", cli._refused_widens(u))

    def test_nothing_refused_says_nothing(self):
        scope_mod.widen(self.conn, self.cfg, task_id="t-w",
                        add=["docs/NOTES.md"], why=GOOD)
        u = scope_mod.usage(self.conn, self.cfg, "t-w")
        self.assertEqual("", cli._refused_widens(u))

    def test_the_files_reached_have_a_reader(self):
        scope_mod.widen(self.conn, self.cfg, task_id="t-w",
                        add=["docs/NOTES.md"], why=GOOD)
        u = scope_mod.usage(self.conn, self.cfg, "t-w")
        self.assertEqual(1, u["reached"])
        out = io.StringIO()
        args = types.SimpleNamespace(repo=str(self.root), task="t-w",
                                     action="show", add=None, drop=None,
                                     acceptance=None)
        with contextlib.redirect_stdout(out):
            cli.cmd_scope(args)
        self.assertIn(f"{u['reached']} of {u['repo_files']} files",
                      out.getvalue())


class WhatCoverPrintsAboutWhatItRefused(unittest.TestCase):
    #: Two clauses, so the second can go unquoted. A newline is one of the
    #: boundaries `request_cover._CLAUSE` splits on; a bare comma is not, and
    #: that is deliberate there.
    REQUEST = ("add the retry ceiling to the sender\n"
               "and separately write the runbook page nobody has")

    def setUp(self):
        self.conn, self.cfg, self.root = _repo(self)
        lifecycle.open_task(self.conn, self.cfg, task_id="t-c",
                            request=self.REQUEST, scope_globs=["app/**"])

    def _show(self):
        out = io.StringIO()
        args = types.SimpleNamespace(repo=str(self.root), task="t-c", show=True,
                                     withdraw=False, quote=None, symbol=None,
                                     test=None, not_done=False, why=None,
                                     acceptance=None)
        with contextlib.redirect_stdout(out):
            cli.cmd_cover(args)
        return out.getvalue()

    def test_a_clause_nobody_quoted_is_named(self):
        request_cover.record(
            self.conn, task_id="t-c", request=self.REQUEST,
            quote="add the retry ceiling to the sender", symbol="app/x.py::x",
            test="", not_done=False, why="",
            acceptance="a fourth attempt is refused rather than sent",
            min_chars=self.cfg.thresholds["min_chars"], root=self.root)
        shown = self._show()
        self.assertIn("nobody quoted at all", shown)
        self.assertIn("runbook page", shown)

    def test_an_entry_measure_threw_out_is_not_shown_as_delivered(self):
        """Recorded against a symbol, and then the symbol went away.

        `record` runs `fault` too, so an entry cannot be written broken -- it
        becomes broken later, which is why `measure` re-runs the same check and
        why dropping its answer mattered. `entries()` still returns the row, and
        the listing printed it as delivered while the ratio above had already
        refused to count it.
        """
        request_cover.record(
            self.conn, task_id="t-c", request=self.REQUEST,
            quote="add the retry ceiling to the sender", symbol="app/x.py::x",
            test="", not_done=False, why="",
            acceptance="a fourth attempt is refused rather than sent",
            min_chars=self.cfg.thresholds["min_chars"], root=self.root)
        (self.root / "app" / "x.py").unlink()

        m = request_cover.measure(self.REQUEST,
                                  request_cover.entries(self.conn, "t-c"),
                                  root=self.root)
        self.assertTrue(m["faults"], "the symbol's file is gone")
        shown = self._show()
        self.assertIn("REJECTED", shown)
        self.assertNotIn("→ app/x.py::x", shown,
                         "an entry measure refused must not read as delivered")
        self.assertIn("1 rejected", shown)

    def test_the_unspoken_printer_is_quiet_when_there_is_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._print_unspoken({"unspoken": []})
        self.assertEqual("", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
