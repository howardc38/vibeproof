"""`lifecycle.check` -- the branch that names what held an expensive claim back.

    python3 -m unittest tests.test_the_kinds_that_held_it_back -v

`debc9b1` made `SKIPPED_EXPENSIVE` carry the kinds that held it, because the
list was built and read nowhere and `cmd_check` printed "something cheaper is
still failing" naming none of them. It wrote `sorted({r["kind"] for r in
failed_cheap})` -- and `failed_cheap` holds kind *names*: `failed_cheap.append(
row["kind"])` is the only line that writes it.

So from that commit until this one, every `v4 check` where a cheap claim failed
and an expensive claim was still to come died with `TypeError: string indices
must be integers, not 'str'`. Not a wrong answer: no answer at all, for the
whole command. It reached this repo the first time a task had both, which is
the ordinary case.

The red-green for `debc9b1` did not run this branch. It needs a claim of a kind
whose *measured median* cost is over 30 seconds and a cheaper one that fails,
and `kind_cost` reads the ledger -- so a fresh fixture reads every kind as
free. This file gives the ledger that history, registers two real checkers, and
runs `lifecycle.check` with nothing stubbed.

Fails against debc9b1..e46bd85.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import config, ledger, lifecycle  # noqa: E402

#: Two checkers, because the scheduler is what is under test and it needs
#: something real to schedule. A stub in place of `runner.run_checker` would
#: also have to stand in for the registry lookup, the `reads` gate and
#: `runner.record` -- three decisions this branch depends on -- and a test that
#: replaces all of them proves something about the mock.
CHECKER = """#!/usr/bin/env python3
import sys
sys.exit({code})
"""


class WhatHeldTheExpensiveClaimBack(unittest.TestCase):
    """One cheap claim that fails, one expensive claim that is not reached."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=self.root, capture_output=True)
        (self.root / ".v4").mkdir()
        (self.root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"cheap": {"checker": "cheap", "staleness": "subject",
                       "question_template": "q"},
             "slow": {"checker": "slow", "staleness": "subject",
                      "question_template": "q"}}))
        (self.root / "checkers").mkdir()
        registry = {}
        for name, code in (("cheap", 1), ("slow", 0)):
            src = CHECKER.format(code=code)
            p = self.root / "checkers" / f"{name}.py"
            p.write_text(src)
            p.chmod(0o755)
            registry[name] = {
                "path": f"checkers/{name}.py", "kinds": [name],
                "reads": ["**/*.py"], "timeout_sec": 60,
                "sha256": hashlib.sha256(src.encode()).hexdigest(),
                "fixtures": ""}
        (self.root / ".v4" / "checkers.json").write_text(json.dumps(registry))
        (self.root / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.root,
                       capture_output=True)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = config.RepoConfig(self.root)

    def _history_making_slow_expensive(self):
        """`kind_cost` is a median over what this repo has actually paid.

        A ceiling somebody wrote down would not do: that function reads the
        ledger on purpose, so the only way to make a kind expensive is for it
        to have run and taken the time.
        """
        ledger.insert(self.conn, "task", id="t-old", request="r" * 80,
                      scope_globs=["**"], base_commit="x", created_at="2026")
        for i in range(3):
            ledger.insert(self.conn, "claim", id=f"old{i}", task_id="t-old",
                          kind="slow", file="a.py", symbol="", variant="",
                          question="q", subject_refs="[]", checker="slow",
                          detector="d", origin="derive", created_at="2026")
            ledger.append_attempt(
                self.conn, claim_id=f"old{i}", subject_digest="{}",
                checker_sha="s", config_sha="cf", head_commit="hc",
                worktree="w", argv="[]", exit_code=0, stdout="", stderr="",
                started_at="2026", ended_at="2026",
                duration_ms=lifecycle.EXPENSIVE_MS * 2,
                claim_digest=ledger.claim_digest(self.conn, f"old{i}"),
                facts_sha="")
        self.conn.commit()

    def _claims(self, *pairs):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(self.conn, "task", id="t", request="r" * 80,
                      scope_globs=["**"], base_commit=head, created_at="2026")
        for cid, kind in pairs:
            ledger.insert(self.conn, "claim", id=cid, task_id="t", kind=kind,
                          file="a.py", symbol="", variant="", question="q",
                          subject_refs=json.dumps(
                              [{"kind": "file", "path": "a.py"}]),
                          checker=kind, detector="d", origin="derive",
                          created_at="2026")
        self.conn.commit()

    def _check(self, run_expensive=False):
        return list(lifecycle.check(self.conn, self.cfg, "t",
                                    run_expensive=run_expensive))

    def test_the_expensive_claim_is_skipped_and_says_what_held_it(self):
        self._history_making_slow_expensive()
        self._claims(("c-cheap", "cheap"), ("c-slow", "slow"))
        rows = self._check()
        skipped = [(r, res) for r, cached, res in rows
                   if cached == "SKIPPED_EXPENSIVE"]
        self.assertEqual(len(skipped), 1, rows)
        row, held = skipped[0]
        self.assertEqual(row["id"], "c-slow")
        self.assertEqual(held, ["cheap"],
                         "the point of the list is that it names the kind")

    def test_nothing_is_skipped_when_the_cheap_claim_passes(self):
        """The control: it is the failure doing this, not the cost alone."""
        self._history_making_slow_expensive()
        self._claims(("c-ok", "slow"), ("c-slow", "slow"))
        self.assertEqual([c for _r, c, _res in self._check()
                          if c == "SKIPPED_EXPENSIVE"], [])

    def test_run_expensive_reaches_it_anyway(self):
        """`--all` says pay for it, and it still does."""
        self._history_making_slow_expensive()
        self._claims(("c-cheap", "cheap"), ("c-slow", "slow"))
        rows = self._check(run_expensive=True)
        self.assertEqual([c for _r, c, _res in rows
                          if c == "SKIPPED_EXPENSIVE"], [])
        self.assertIn("c-slow", [r["id"] for r, _c, _res in rows])

    def test_two_cheap_failures_are_named_once(self):
        """The set, which is why the line does more than `list()`."""
        self._history_making_slow_expensive()
        self._claims(("c1", "cheap"), ("c2", "cheap"), ("c-slow", "slow"))
        held = [res for _r, cached, res in self._check()
                if cached == "SKIPPED_EXPENSIVE"]
        self.assertEqual(held, [["cheap"]])


if __name__ == "__main__":
    unittest.main()
