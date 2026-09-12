"""What CI was given, and what it assumed it had been given.

    python3 -m unittest tests.test_the_ground_ci_stands_on -v

Four facts about `.github/workflows/v4.yml`, all of them measured on runs that
had already happened:

* The `Tests` step failed on all nine pushes from 2026-08-26T05:01 -- run
  33048630308 logs `fetch-depth: 1` and then `near-miss cites commit a9ae5fb,
  which this repo does not have` -- so the facts step and both `accept` steps
  under it never ran once. The same assertion was red in `v4 accept`, whose
  tree is `git init` plus one commit, for the same reason from the other side.
  A clone has the history; neither of those two checkouts did.

* The scheduled `sweep` job asked `v4 sweep`, which reads `.git/v4/ledger.db`,
  in a checkout that never carries one. Measured in a clone of `dbd0a60`:
  `DUE -- last sweep never, and nothing is open`, from a tree whose committed
  export holds two sweeps and whose `repo-review` was open.

* Both third-party actions were pinned to `@v4` and `@v5` -- branches their
  owners move -- so what CI executed was decided outside this repo. They are
  the only external programs this repo declares.

* No `permissions:` block, so all three jobs took the `GITHUB_TOKEN` scope the
  repository or organisation default happens to grant.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import accept, config, ledger, sweep  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "v4.yml"

#: `uses:` and its value, as a step writes it.  A line that starts with `#` does
#: not match: after the leading whitespace the next character has to be `-` or
#: `uses`, so the commented examples in this file's own prose are not steps.
_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)")

#: `owner/repo@<40 hex>`, optionally with a path for an action in a subdirectory.
#: Deliberately a shape and not a list of the two shas this repo pins today: the
#: sha lives in the workflow, and a test that repeated it would be a second
#: place to update and a second place to be wrong.
_PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}$")


def _git(cwd, *args, **kw):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, **kw)


def _repo(case, files=None):
    """A throwaway git repo with `.v4/config.json`, one commit, and a name."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"repo": tmp.name, "test_command": "true",
         "policy": "allow_accepted_risk",
         "lens_sweep": {"every_days": 4, "weekday": None,
                        "not_before_hour": None}}))
    (tmp / ".v4" / "checkers.json").write_text("{}")
    for rel, text in (files or {}).items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(tmp, "add", "-A")
    _git(tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    return tmp


class TheTreeUnderTestIsAsDeepAsAClone(unittest.TestCase):
    """`accept.archive` said "no local state" and built "no history".

    Those are different things. `.git/v4/ledger.db` is untracked and is the
    whole reason the archive exists; the object store is tracked history and
    every clone carries it. The gap made `v4 accept` report 6/7 with `tests`
    red on an assertion about a lens, which is a fact about `archive` reported
    as a fact about the tree being accepted.
    """

    def setUp(self):
        self.src = _repo(self, {"a.txt": "one\n"})
        (self.src / "a.txt").write_text("two\n")
        _git(self.src, "add", "-A")
        _git(self.src, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "two")
        self.old = _git(self.src, "rev-parse", "HEAD~1").stdout.strip()
        self.into = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.into, ignore_errors=True)

    def _resolves(self, tree, sha):
        return _git(tree, "cat-file", "-e", sha + "^{commit}").returncode == 0

    def test_a_commit_of_the_source_repo_resolves_in_the_archive(self):
        """The question `tests/test_a_lens_reaches_the_reviewer.py` puts, asked
        of the tree `v4 accept` runs it in."""
        tree = accept.archive(self.src, self.into)
        self.assertTrue(self._resolves(tree, self.old),
                        f"{self.old[:12]} is in the repo being accepted and not "
                        f"in the tree accepting it")

    def test_head_is_still_one_commit_built_from_the_index(self):
        """The property the history must not have cost.

        `git write-tree`, not HEAD, is what makes a staged change visible to
        `v4 accept` -- so a tree that had become a clone of HEAD would pass the
        test above and quietly stop accepting the work in front of it.
        """
        (self.src / "staged.txt").write_text("only in the index\n")
        _git(self.src, "add", "-A")
        tree = accept.archive(self.src, self.into)
        self.assertEqual(
            _git(tree, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        self.assertTrue((tree / "staged.txt").is_file(),
                        "the archive stopped being built from the index")

    def test_the_source_is_read_as_a_path_of_its_own(self):
        """Found by breaking it: the fetch runs with `cwd` at the new tree while
        every other git call in `archive` runs at the source, so an unresolved
        `Path(".")` made the tree fetch from itself, exit 0, and resolve
        nothing."""
        here = Path.cwd()
        os.chdir(self.src.parent)
        self.addCleanup(os.chdir, here)
        tree = accept.archive(Path(self.src.name), self.into)
        self.assertTrue(self._resolves(tree, self.old))


class ASweepAnsweredWhereTheLedgerIsNot(unittest.TestCase):
    """`due()` reads a database a checkout does not carry, and `ledger.connect`
    makes an empty one rather than failing -- so the scheduled job's answer was
    fixed before it looked at anything."""

    def setUp(self):
        self.tmp = _repo(self)
        conn = ledger.connect(self.tmp)
        ledger.insert(conn, "event", kind="lens_reviewed", actor="reviewer", payload={"lens":"devx","findings":0}, created_at="2020-01-01T00:00:00+00:00")
        sweep.record(conn, lenses=["devx"], findings=0)
        ledger.export_jsonl(conn, self.tmp / ".v4" / "ledger_export.jsonl",
                            self.tmp)
        conn.close()
        self.cfg = config.RepoConfig(self.tmp)
        self.when = sweep.last(ledger.connect(self.tmp))
        # What a clone is: the tracked export, and no database beside it.
        shutil.rmtree(self.tmp / ".git" / "v4")

    def test_the_export_carries_the_sweep_the_database_no_longer_does(self):
        self.assertEqual(sweep.last_exported(self.tmp), self.when)
        self.assertIsNone(sweep.last(ledger.connect(self.tmp)),
                          "a checkout was expected to carry no ledger")

    def test_a_checkout_no_longer_reports_a_repo_that_never_swept(self):
        """The measured sentence: `DUE -- last sweep never`, in a clone whose
        committed export held the sweeps."""
        _due, why = sweep.due_from_export(self.tmp, self.cfg,
                                          now=self.when + timedelta(days=5))
        self.assertNotIn("never", why)
        self.assertIn("5d ago", why)

    def test_the_interval_still_holds_it(self):
        due, why = sweep.due_from_export(self.tmp, self.cfg,
                                         now=self.when + timedelta(days=1))
        self.assertFalse(due)
        self.assertIn("interval", why)

    def test_and_lets_go_once_it_has_passed(self):
        due, _why = sweep.due_from_export(self.tmp, self.cfg,
                                          now=self.when + timedelta(days=5))
        self.assertTrue(due)

    def test_the_two_sources_do_not_get_to_disagree(self):
        """One schedule, two places the timestamp comes from. `elapsed` is the
        arithmetic both ask, so a repo set to "Mondays only" cannot mean one
        thing on a laptop and another in CI."""
        for days in (0, 1, 3, 4, 5, 40):
            now = self.when + timedelta(days=days)
            from_export = sweep.due_from_export(self.tmp, self.cfg, now=now)[0]
            from_clock = sweep.elapsed(self.cfg, self.when, now)[0]
            self.assertEqual(from_export, from_clock, f"at +{days}d")

    def test_it_says_which_half_of_the_question_it_did_not_put(self):
        """A checkout cannot see whether somebody is mid-task, and the sentence
        being replaced claimed it had looked."""
        _due, why = sweep.due_from_export(self.tmp, self.cfg,
                                          now=self.when + timedelta(days=5))
        self.assertNotIn("nothing is open", why)
        self.assertIn("not asked", why)

    def test_no_export_is_refused_rather_than_read_as_never(self):
        (self.tmp / ".v4" / "ledger_export.jsonl").unlink()
        with self.assertRaises(FileNotFoundError) as caught:
            sweep.due_from_export(self.tmp, self.cfg)
        self.assertIn("v4 ship", str(caught.exception))


class WhatTheWorkflowDeclaresAboutItsOwnGround(unittest.TestCase):
    """Read as lines, not parsed: PyYAML is not a dependency of this repo and
    adding one so a test can read four keys would be the larger change."""

    def setUp(self):
        self.lines = WORKFLOW.read_text(encoding="utf-8").splitlines()

    def _jobs(self):
        """{name: [lines]} -- each block under `jobs:`, split at indent two."""
        out, name = {}, None
        inside = False
        for line in self.lines:
            if line.rstrip() == "jobs:":
                inside = True
                continue
            if not inside:
                continue
            if line.strip() and not line.startswith(" "):
                break                      # back to column zero: `jobs:` is over
            head = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if head:
                name = head.group(1)
                out[name] = []
            elif name is not None:
                out[name].append(line)
        return out

    def test_every_third_party_action_is_pinned_to_a_commit(self):
        used = [m.group(1) for m in
                (_USES.match(line) for line in self.lines) if m]
        self.assertTrue(used, "no `uses:` found -- the parse, not the workflow")
        for ref in used:
            self.assertRegex(
                ref, _PINNED,
                f"{ref} names a tag or a branch. A tag is a name its owner can "
                f"move, so what CI runs would be decided outside this repo.")

    def test_the_token_scope_is_stated_here_and_not_inherited(self):
        """A repository default is a setting outside this file that can change
        without a commit in it."""
        self.assertTrue(any(line.rstrip() == "permissions:" for line in self.lines),
                        "no workflow-level `permissions:` block, so every job "
                        "takes whatever the repository default grants")

    def test_the_job_that_runs_the_suite_checks_out_a_whole_clone(self):
        """Tied to `test_command` rather than to a job name: the reason the
        checkout has to be deep is that the suite resolves commits, and the
        suite is whichever step runs that command."""
        cmd = json.loads((ROOT / ".v4" / "config.json").read_text())["test_command"]
        running = {name: block for name, block in self._jobs().items()
                   if any(cmd in line.split("#")[0] for line in block)}
        self.assertTrue(running, f"no job runs {cmd!r}")
        for name, block in running.items():
            self.assertTrue(
                any(re.search(r"fetch-depth:\s*0\b", line) for line in block),
                f"job {name} runs the suite in a shallow checkout, and the suite "
                f"resolves commits the lenses cite")

    def test_no_step_throws_away_its_own_exit_code(self):
        """`v4 sweep || true` printed the same line whether the command worked,
        found nothing, or crashed."""
        swallowing = [line for line in self.lines
                      if re.search(r"\|\|\s*(true|:)\s*$", line)]
        self.assertEqual(swallowing, [])


class AFailingSuiteSaysWhereToLook(unittest.TestCase):
    """`accept` kept the last line, and the last line of a failure is a count.

    `FAILED (failures=2, skipped=1)` says how many and never which, and
    `cmd_accept` runs in a `TemporaryDirectory` that is gone before anybody
    reads the row -- so the names were recoverable only by rebuilding the
    archive and running the suite again. Measured 2026-08-27: two sessions hit
    this the same day and one never recovered them.
    """

    def _detail(self, code, out):
        return accept._test_detail("the-declared-command", code, out)

    def test_a_pass_still_says_the_one_line_worth_saying(self):
        self.assertEqual(self._detail(0, "...\nOK (skipped=1)\n"),
                         "OK (skipped=1)")

    def test_a_failure_names_a_file_that_holds_the_whole_output(self):
        out = "\n".join(["noise"] * 500
                        + ["FAIL: test_the_one_that_broke",
                           "FAILED (failures=1)"])
        detail = self._detail(1, out)
        path = detail.split("whole output: ")[-1].strip()
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        self.assertTrue(Path(path).is_file(), detail)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), out)
        self.assertIn("test_the_one_that_broke", Path(path).read_text())

    def test_and_the_row_itself_carries_more_than_the_count(self):
        """The row is what a person sees first. One line was the count alone."""
        detail = self._detail(1, "FAIL: test_a\nFAIL: test_b\nFAILED (failures=2)")
        self.addCleanup(lambda: Path(detail.split("whole output: ")[-1].strip())
                        .unlink(missing_ok=True))
        self.assertIn("test_a", detail)
        self.assertIn("test_b", detail)

    def test_a_green_run_leaves_no_file_to_ignore(self):
        """A log per passing accept is litter, and litter is how a line stops
        being read."""
        before = set(Path(tempfile.gettempdir()).glob("v4-accept-tests-*.log"))
        self._detail(0, "OK")
        self.assertEqual(
            set(Path(tempfile.gettempdir()).glob("v4-accept-tests-*.log")),
            before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
