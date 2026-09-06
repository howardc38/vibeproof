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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: The reason the case below stands down, kept where a test can read it.
#:
#: The branch that prints it was added and never once executed by a gate: the
#: task that added it recorded a single test run, in this repo, where `git
#: ls-files .v4/risks` returns 180 records -- so the skip was reached by hand,
#: in a tree built with `git archive`, and by nothing else. Two things about it
#: were therefore unpinned, and both are now: that the branch is reached at all
#: when the records are absent, and that its wording stays out of the oracle's
#: environment class. That second one is not cosmetic -- a reason that reads as
#: "the world was smaller" makes `run_without_silent_skips` exit 1, and it is
#: the sole oracle for every `test` claim in this repo.
NO_RECORDS_HERE = (
    "no .v4/risks/*.json in this checkout, so whether a written signature "
    "carries `signed_by` and `stdin_was_a_tty` goes unchecked here. A release "
    "tree excludes those records deliberately; the three cases beside this one "
    "still run, and they are the ones about what a person is shown -- "
    "`risk.attribution` is called directly with an agent record, a person "
    "record and a record from before the field existed.")


def _signed_records(root):
    """The written signature records in a checkout, newest last.

    A function so the empty case can be asked for. Inline, the only way to
    reach it was to be standing in a tree that had none.
    """
    return sorted(Path(root, ".v4", "risks").glob("*.json"))

from kernel import cli as cli_mod                               # noqa: E402
from kernel import config as config_mod                         # noqa: E402
from kernel import ledger as ledger_mod                         # noqa: E402
from kernel import review as review_mod                         # noqa: E402
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
    # The least a `RepoConfig` will load. Written here rather than in the one
    # case that needs it, because every caller of this helper is a repo and a
    # repo without this file is not one.
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk",
         "thresholds": {"min_chars": 40}}))
    (tmp / ".v4" / "checkers.json").write_text("{}")
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
        signed = _signed_records(ROOT)
        if not signed:
            self.skipTest(NO_RECORDS_HERE)
        rec = json.loads(signed[-1].read_text(encoding="utf-8"))
        self.assertIn("signed_by", rec)
        self.assertIn("stdin_was_a_tty", rec)


class TheStandDownIsReachableAndStaysQuiet(unittest.TestCase):
    """The branch above, asked directly, and the thing that decides its cost.

    It was added and never executed by a gate. The task that added it recorded
    one test run, in this repo, where `.v4/risks/` holds 180 records -- so the
    branch was reached by hand in a tree built with `git archive` and by
    nothing else. Whether it leaves the suite green turns entirely on its
    wording not matching `run_without_silent_skips.ENVIRONMENT`, and nothing
    said so.
    """

    def test_a_tree_with_no_records_reaches_it(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4" / "risks").mkdir(parents=True)
        self.assertEqual(_signed_records(tmp), [],
                         "this is the state a release tree is in")

    def test_a_new_signature_is_found_without_private_historical_records(self):
        """Exercise the real writer in a fresh repo, including public clones.

        A non-empty control must not depend on the maintainer's risk archive.
        The separate historical test still reports when that archive is absent.
        """
        from unittest import mock
        tmp, conn = _repo(self, with_attempt=True)
        self.addCleanup(conn.close)
        with mock.patch.object(sys, "stdin", io.StringIO()):
            _record, path = risk.accept(
                conn, config_mod.RepoConfig(tmp), claim_id="c", kind="unprovable",
                why="This isolated fixture verifies signer attribution and writes no production decision.",
                require_tty=False)
        self.assertEqual([p.resolve() for p in _signed_records(tmp)], [path.resolve()])
        stored = json.loads(path.read_text())
        self.assertEqual(stored["signed_by"], "agent")
        self.assertIs(stored["stdin_was_a_tty"], False)

    def test_the_reason_stays_out_of_the_environment_class(self):
        """A reason that reads as "the world was smaller" makes the sole oracle
        for every `test` claim exit 1. This one is a decision, not an absence:
        a release tree excludes those records on purpose."""
        import tests.run_without_silent_skips as oracle
        self.assertIsNone(oracle.ENVIRONMENT.search(NO_RECORDS_HERE),
                          NO_RECORDS_HERE)

    def test_and_a_real_absence_would_still_be_loud(self):
        """The control on the same pattern, so this is not passing because the
        pattern matches nothing."""
        import tests.run_without_silent_skips as oracle
        self.assertIsNotNone(oracle.ENVIRONMENT.search(
            "the git binary is not installed here"))


class AnOfferIsNotAVerdict(unittest.TestCase):
    """What `review close` prints, read from its output rather than its source.

    Both of these grepped `kernel/cli.py` for the words it prints. Wrapping
    that `print` in `if False:` left both green -- the mutation
    `tests/test_nothing_seen.py` records as having survived 777 tests before
    the test there was rewritten to call the function and assert on captured
    stdout. `cmd_review` is already executed by this suite, so the runtime
    assertion was available and simply not made.
    """

    def _close(self, **kw):
        """`v4 review close` against a throwaway repo, and what it printed."""
        root, conn = _repo(self)
        self.addCleanup(conn.close)
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding", "detector": None,
                                "staleness": "subject", "engagement": True,
                                "question_template": "is {file} closed"}}))
        (root / "mod.py").write_text("def helper():\n    return 1\n")
        (root / "t_mod.py").write_text("def test_it():\n    pass\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "b"], cwd=root, capture_output=True)
        cid, _made, _sib = review_mod.raise_finding(
            conn, config_mod.RepoConfig(root), task_id=None, file="mod.py",
            symbol="helper", note="a finding that wants closing")
        conn.commit()
        args = SimpleNamespace(
            repo=str(root), action="close", claim=[cid], test="t_mod.py",
            command="python3 -m unittest t_mod", parent="HEAD", gone=None,
            now=None, why=None, target=None, task=None, file=None, symbol=None,
            note=None, lens=None, name=None, findings=None, withdraw=False,
            mutation_file=None, mutation_gone=None, mutation_now="")
        for k, v in kw.items():
            setattr(args, k, v)
        said = io.StringIO()
        with redirect_stdout(said):
            code = cli_mod.cmd_review(args)
        return code, said.getvalue(), cid

    def test_review_close_says_it_has_not_been_checked(self):
        code, said, _cid = self._close()
        self.assertEqual(code, 0, said)
        self.assertIn("NOT VERIFIED YET", said)
        self.assertIn("the claim is still open", said)

    def test_and_names_the_command_that_gives_the_verdict(self):
        """A next step somebody can run, not a condition somebody has to turn
        into one. The task it names is the standing review task, because that is
        where a finding hangs."""
        _code, said, cid = self._close()
        self.assertIn(f"check --task {ledger_mod.REVIEW_TASK}", said)
        self.assertIn(cid, said)
        self.assertEqual(ledger_mod.REVIEW_TASK, "repo-review")


if __name__ == "__main__":
    unittest.main()
