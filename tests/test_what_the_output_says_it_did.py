"""Four lines that answered to an internal state and said something else.

    python3 -m unittest tests.test_what_the_output_says_it_did -v

`CLAUDE.md` carries the rule these break: "「工具跑過了」和「閘通過了」是兩個命名
空間。絕不合併。" Each of these merged two, and each was found by somebody acting
on the wrong one.

  * `N/M terminal` counted everything not blocking, so an OPEN claim of a
    `report` kind was reported as terminal -- printed by this repo's own ship,
    `14/14 terminal` beside a claim the same page listed OPEN
  * `v4 review close` prints the condition a closure has to meet and exits 0,
    and the claim is still open; an adopter read that as success on 35 findings
  * `risk accept --no-tty-check` printed "signed by <a person's email>" while
    writing `signed_by: agent` into the record
  * `v4 audit` said `chain: BROKEN` on a repo that had never shipped, which is
    the first thing a new adopter runs

All four fail against the commit before this file.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as ledger_mod                         # noqa: E402
from kernel import risk                                         # noqa: E402
from kernel import state                                        # noqa: E402


class TerminalIsAWordWithADefinition(unittest.TestCase):
    def test_the_definition_is_where_it_always_was(self):
        self.assertEqual(state.TERMINAL,
                         {"ANSWERED", "RISK_ACCEPTED", "RETRACTED"})

    def test_an_open_report_kind_is_not_terminal(self):
        """The arithmetic the printed line and the JSON both do now. `blocked`
        excludes a report kind by design -- that is what a report kind is -- so
        `len(rows) - len(blocked)` answers "would this ship", and the word on it
        answered a different question."""
        rows = [({"id": "a"}, "ANSWERED"), ({"id": "b"}, "OPEN"),
                ({"id": "c"}, "RISK_ACCEPTED")]
        blocked = []                       # `b` is a report kind: open, not blocking
        settled = sum(1 for _, st in rows if st in state.TERMINAL)
        self.assertEqual(settled, 2)
        self.assertEqual(len(rows) - len(blocked), 3)
        self.assertNotEqual(settled, len(rows) - len(blocked),
                            "if these were equal the distinction would be free")

    def test_both_numbers_reach_a_consumer(self):
        """Run, not read. `--json` is what something scripting against this
        gets, and `terminal` there was the not-blocking count under the other
        one's name -- so a consumer could not tell an answered claim from an
        open report kind. Asked of this repo's own ledger, through the CLI."""
        r = subprocess.run(
            [str(ROOT / "bin" / "v4"), "--repo", str(ROOT), "status",
             "--task", "t-w2-mechanisms", "--json"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode in (0, 1), True, r.stderr[:400])
        blob = json.loads(r.stdout)
        self.assertIn("terminal", blob)
        self.assertIn("not_blocking", blob)
        self.assertEqual(blob["not_blocking"],
                         blob["total"] - len(blob["open"]))
        self.assertLessEqual(blob["terminal"], blob["not_blocking"])

    def test_and_a_stale_claim_is_not_terminal_either(self):
        """The other half of the same word: STALE is not in the set, so a task
        whose answers expired does not report them as reached."""
        self.assertNotIn("STALE", state.TERMINAL)


def _repo(case, *, with_attempt=False):
    """A git repo -- `ledger.connect` asks git where the common dir is, because
    the ledger lives beside it rather than in the worktree."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"]):
        subprocess.run(a, cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    conn = ledger_mod.connect(tmp)
    if with_attempt:
        # Through the kernel: the ledger refuses a direct insert into `claim`,
        # and `append_attempt` is the only thing that grows the chain. A row
        # forced past either would not be the state this is about.
        when = "2026-09-04T00:00:00+00:00"
        with ledger_mod.writing(conn):
            ledger_mod.insert(conn, "task", id="t", request="r",
                              scope_globs=["**"], base_commit="", created_at=when)
            ledger_mod.insert(conn, "claim", id="c", task_id="t", kind="test",
                              question="q", subject_refs=[], checker="test",
                              origin="derive", file=None, symbol=None,
                              variant=None, line=None, note=None, detector=None,
                              detector_sha=None, created_at=when)
        ledger_mod.append_attempt(
            conn, claim_id="c", subject_digest="{}", checker_sha="s",
            config_sha="cf", head_commit="hc", worktree="w", argv="[]",
            exit_code=0, stdout="", stderr="", started_at=when, ended_at=when,
            duration_ms=1, facts_sha="")
    return tmp, conn


class AnAnchorThatWasNeverWrittenIsNotAnAnchorThatWasRemoved(unittest.TestCase):
    def test_a_repo_that_has_never_shipped_says_so(self):
        tmp, conn = _repo(self)
        _ok, problems = ledger_mod.audit_chain(conn, tmp)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("nothing has been checked here yet", problems[0])
        self.assertIn("first `v4 ship` writes it", problems[0])

    def test_a_repo_with_attempts_and_no_anchor_still_gets_the_warning(self):
        """The half that must not soften. Rows exist, the anchor does not, and
        that is the case the anchor was introduced for."""
        tmp, conn = _repo(self, with_attempt=True)
        _ok, problems = ledger_mod.audit_chain(conn, tmp)
        self.assertTrue(problems)
        self.assertIn("a truncated or rebuilt ledger verifies clean",
                      " ".join(problems))

    def test_both_are_still_problems(self):
        """Not a downgrade to a warning: an unanchored chain is unanchored
        either way, and `v4 audit` exits 1 for both. Only the sentence differs."""
        for with_attempt in (False, True):
            tmp, conn = _repo(self, with_attempt=with_attempt)
            ok, problems = ledger_mod.audit_chain(conn, tmp)
            self.assertFalse(ok, problems)
            self.assertTrue(problems)


class WhoSignedIsNotWhoseMachineItWas(unittest.TestCase):
    def test_an_agent_signature_does_not_read_as_a_person(self):
        said = risk.attribution({"who": "someone@example.com",
                                 "signed_by": "agent",
                                 "at": "2026-09-04T00:00:00+00:00"})
        self.assertIn("signed as agent", said)
        self.assertIn("git identity on this machine", said)
        self.assertNotIn("signed by someone@example.com", said)

    def test_a_person_still_reads_as_a_person(self):
        """The other half. A signature somebody actually gave says so plainly,
        or this is not a repair, it is a different wrong sentence."""
        said = risk.attribution({"who": "someone@example.com",
                                 "signed_by": "person",
                                 "at": "2026-09-04T00:00:00+00:00"})
        self.assertEqual(said,
                         "signed by someone@example.com at 2026-09-04T00:00:00+00:00")

    def test_a_record_with_no_signer_field_reads_as_a_person(self):
        """Records written before the field existed. Reading a missing value as
        `agent` would relabel every historical signature in the repo."""
        self.assertIn("signed by x", risk.attribution({"who": "x", "at": "t"}))

    def test_the_record_itself_was_always_honest(self):
        """The file said `signed_by: agent`, `stdin_was_a_tty: false`, and why
        `who` is there. Only the terminal line was wrong, which is the one a
        person reads.

        This read `.v4/risks/*.json` and asserted there were some, which is true
        of the repo it was written in and false of a tree built for release --
        the export excludes signed risk records on purpose. It passed here and
        failed under `v4 accept`, which is what `accept` is for: a check that
        holds only because of local state dies exactly there. Skipped rather
        than deleted, and the skip says what it costs, because the assertion is
        real wherever the records are.
        """
        signed = sorted(Path(ROOT / ".v4/risks").glob("*.json"))
        if not signed:
            self.skipTest(
                "no .v4/risks/*.json in this checkout, so whether a written "
                "signature carries `signed_by` and `stdin_was_a_tty` goes "
                "unchecked here. A release tree excludes those records "
                "deliberately; the three cases beside this one still run, and "
                "they are the ones about what a person is shown -- "
                "`risk.attribution` is called directly with an agent record, a "
                "person record and a record from before the field existed.")
        rec = json.loads(signed[-1].read_text(encoding="utf-8"))
        self.assertIn("signed_by", rec)
        self.assertIn("stdin_was_a_tty", rec)


class AnOfferIsNotAVerdict(unittest.TestCase):
    def test_review_close_says_it_has_not_been_checked(self):
        src = (ROOT / "kernel" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("NOT VERIFIED YET", src)
        self.assertIn("the claim is still open", src)

    def test_and_names_the_command_that_gives_the_verdict(self):
        """A next step somebody can run, not a condition somebody has to turn
        into one. The task it names is the standing review task, because that is
        where a finding hangs."""
        src = (ROOT / "kernel" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("ledger.REVIEW_TASK", src)
        self.assertEqual(ledger_mod.REVIEW_TASK, "repo-review")


if __name__ == "__main__":
    unittest.main()
