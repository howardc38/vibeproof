"""The stop gate resolved a task from the repo and then asked whoever stopped.

    python3 -m unittest tests.test_which_session_is_being_asked -v

`_repo_and_task` reads `V4_TASK`, or else the one task the ledger has open.
Neither is a session. Two agents in one checkout -- or one agent watching a repo
it is not working in -- both stop, and both are handed whatever task happened to
be open.

Measured 2026-08-26 in this repo: a session holding a standing read-only
instruction was told to `ship fw-five`, then to `derive fw-stranded`, then to
answer or sign thirteen claims on `fw-baseline`, more than twenty times over
several hours. It had touched none of them. The gate's own docstring calls the
repeat "a loop, and this is friction rather than a boundary"; this is the same
argument about *who* rather than *how often*.

The fix needs a fact nobody was recording: which session did the work. A hook is
the only place in this system that can know -- `v4` is a subprocess the agent
starts, and nothing tells it which session it belongs to -- so `hook_seen` now
carries it, measured against a real `PreToolUse` and a real `Stop` payload
rather than read off documentation.

All of it fails against the commit before this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

import _framework                                               # noqa: E402
import stop_gate                                                # noqa: E402
from kernel import ledger                                       # noqa: E402

MINE = "session-that-did-the-work"
THEIRS = "session-that-only-watched"


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    conn = ledger.connect(tmp)
    case.addCleanup(conn.close)
    ledger.insert(conn, "task", id="t-theirs", request="r",
                  scope_globs=["app/**"], base_commit="", created_at="2026")
    return tmp, conn


class WhatARecordedMarkNowCarries(unittest.TestCase):
    def test_record_seen_puts_the_session_in_the_payload(self):
        tmp, conn = _repo(self)
        self.assertEqual(
            _framework.record_seen(tmp, "t-theirs", "app/x.py", session=MINE), "")
        row = conn.execute(
            "SELECT payload FROM event WHERE kind='hook_seen'").fetchone()
        self.assertEqual(json.loads(row["payload"])["session"], MINE)

    def test_and_leaves_it_out_when_there_is_none(self):
        """An absent key and an empty string are different answers, and only
        one of them can be told apart from a row written before this existed."""
        tmp, conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py")
        row = conn.execute(
            "SELECT payload FROM event WHERE kind='hook_seen'").fetchone()
        self.assertNotIn("session", json.loads(row["payload"]))


class WhoseTaskThisIs(unittest.TestCase):
    def test_a_session_that_never_touched_it_is_not_the_worker(self):
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py", session=THEIRS)
        self.assertFalse(stop_gate._worked_here(tmp, "t-theirs", MINE))

    def test_the_session_that_did_the_work_is(self):
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py", session=MINE)
        self.assertTrue(stop_gate._worked_here(tmp, "t-theirs", MINE))

    def test_a_task_with_no_tagged_marks_still_asks(self):
        """Opened before this field existed, or worked with the hooks off.
        Nothing tells the two apart, and switching the gate off for every older
        task would be a worse mistake than asking one session too many."""
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py")
        self.assertTrue(stop_gate._worked_here(tmp, "t-theirs", MINE))

    def test_a_ledger_it_cannot_read_still_asks(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        self.assertTrue(stop_gate._worked_here(tmp, "t-theirs", MINE))

    def test_a_payload_with_no_session_still_asks(self):
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py", session=THEIRS)
        self.assertTrue(stop_gate._worked_here(tmp, "t-theirs", ""))


class TheGateItself(unittest.TestCase):
    """Through `main`, reading a real payload off stdin.

    `_states` shells out to `v4 status --json`, and a bare fixture repo has no
    launcher to run -- it returns `None`, and `main` stands down. That is
    correct behaviour and useless as a control: an assertion that the watcher
    got `{}` would have passed against the unfixed gate too, for a reason that
    has nothing to do with sessions. So the one thing this class is about is
    held fixed, and everything else is left real.
    """

    OPEN = [("OPEN", "c1", "lint")]

    def _run(self, root, session, *, task_env=None):
        import contextlib
        import io
        payload = {"session_id": session, "stop_hook_active": False,
                   "hook_event_name": "Stop", "cwd": str(root)}
        said = io.StringIO()
        old_states, old_repo = stop_gate._states, stop_gate._repo_and_task
        old_task = os.environ.pop("V4_TASK", None)
        if task_env:
            os.environ["V4_TASK"] = task_env
        stop_gate._states = lambda _root, _tid: self.OPEN
        # `_repo_and_task` takes the session now: with more than one task open
        # it narrows by whose marks are on them before it refuses to guess.
        stop_gate._repo_and_task = lambda _session="": (root, "t-theirs")
        try:
            with contextlib.redirect_stdout(said):
                with unittest.mock.patch("sys.stdin",
                                         io.StringIO(json.dumps(payload))):
                    stop_gate.main()
        finally:
            stop_gate._states, stop_gate._repo_and_task = old_states, old_repo
            os.environ.pop("V4_TASK", None)
            if old_task is not None:
                os.environ["V4_TASK"] = old_task
        return json.loads(said.getvalue() or "{}")

    def test_the_watcher_is_let_through(self):
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py", session=THEIRS)
        self.assertEqual(self._run(tmp, MINE), {})

    def test_and_the_worker_is_still_stopped(self):
        """The gate has to keep doing its job for the session it is about, or
        this repair is an off switch wearing a reason."""
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py", session=MINE)
        self.assertEqual(self._run(tmp, MINE).get("decision"), "block")

    def test_a_session_that_named_the_task_itself_is_taken_at_its_word(self):
        """`V4_TASK` is the session saying which task is its own. Nothing else
        in the payload is."""
        tmp, _conn = _repo(self)
        _framework.record_seen(tmp, "t-theirs", "app/x.py", session=THEIRS)
        self.assertEqual(
            self._run(tmp, MINE, task_env="t-theirs").get("decision"), "block")


if __name__ == "__main__":
    unittest.main()
