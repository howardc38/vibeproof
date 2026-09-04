"""Repairs in `kernel/trend.py` and `kernel/lifecycle.py`, called directly.

    python3 -m unittest tests.test_what_the_trend_report_counts -v

`v4 trend` is the only place that says whether the four layers are doing
anything, so a number that counts the wrong rows is the measurement of the
whole design being wrong. `lifecycle` is what the numbers are about.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger, risk, trend  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
        {"lint": {"checker": "lint", "question_template": "q",
                  "staleness": "repo"}}))
    (tmp / ".v4" / "checkers.json").write_text("{}")
    (tmp / ".v4" / "detectors.json").write_text("{}")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                    "commit", "-qm", "base"], cwd=tmp, capture_output=True)
    conn = ledger.connect(tmp)
    case.addCleanup(conn.close)
    return tmp, conn


def _task(conn, tid, at="2026-08-01T00:00:00+00:00"):
    ledger.insert(conn, "task", id=tid, request="r", scope_globs=["**"],
                  base_commit="", created_at=at)


def _event(conn, tid, kind, at, **payload):
    with ledger.writing(conn):
        ledger.insert(conn, "event", task_id=tid, claim_id=None, kind=kind,
                      actor="worker", payload=payload, created_at=at)


class ATaskTheHookNeverSawIsNotATaskWithNoWrites(unittest.TestCase):
    """`engagement_lag` counted `hook_seen` rows and said nothing else.

    A task on which the hook never fired reported zero writes before the first
    sentence -- the same number as a task where the worker wrote the sentence
    first. One is discipline and the other is a hook that was not installed.
    """

    def test_a_task_with_no_hook_rows_is_marked_unwatched(self):
        _root, conn = _repo(self)
        _task(conn, "quiet")
        row = [r for r in trend.engagement_lag(conn) if r["task"] == "quiet"][0]
        self.assertFalse(row["watched"])
        self.assertEqual(row["marks"], 0)

    def test_a_task_the_hook_did_watch_says_so(self):
        """The control: reporting every task as unwatched would pass the test
        above and lose the distinction the other way."""
        _root, conn = _repo(self)
        _task(conn, "loud")
        for i in range(3):
            _event(conn, "loud", "hook_seen", f"2026-08-01T00:0{i}:00+00:00",
                   allowed=1, basis="in-scope")
        row = [r for r in trend.engagement_lag(conn) if r["task"] == "loud"][0]
        self.assertTrue(row["watched"])
        self.assertEqual(row["marks"], 3)


class ATaskWithNoHookRowsSortsLast(unittest.TestCase):
    """`inherited` fell back to the string "9999" for a task's first write.

    `"9999"` sorts above every ISO timestamp, so a task whose hook never fired
    read as the most recent one there is -- and every claim carried into it
    looked inherited from work that had not happened yet.
    """

    def test_a_task_with_no_first_write_is_named_rather_than_sorted(self):
        _root, conn = _repo(self)
        _task(conn, "quiet", "2026-06-01T00:00:00+00:00")
        row = [r for r in trend.inherited(conn) if r["task"] == "quiet"][0]
        self.assertIsNone(row["before_first_write"])
        self.assertFalse(row["watched"])
        self.assertIn("never fired", row["why"])

    def test_a_task_with_one_is_counted_normally(self):
        _root, conn = _repo(self)
        _task(conn, "loud", "2026-06-01T00:00:00+00:00")
        _event(conn, "loud", "hook_seen", "2026-06-01T00:01:00+00:00",
               allowed=1, basis="in-scope")
        row = [r for r in trend.inherited(conn) if r["task"] == "loud"][0]
        self.assertTrue(row["watched"])
        self.assertIsNotNone(row["before_first_write"])


class HowAClaimEndedIsWhatTheStateMachineSays(unittest.TestCase):
    """The three buckets came from the raw last exit code.

    So they disagreed with `state.claim_state`, which is what `ship` reads: a
    retracted claim was counted as open, and a claim whose signature no longer
    covers it was counted as signed.
    """

    def _claim(self, conn, cid, exit_code):
        ledger.insert(conn, "claim", id=cid, task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026-08-01T00:00:00+00:00")
        ledger.append_attempt(conn, claim_id=cid, subject_digest="{}",
                              checker_sha="s", config_sha="c", head_commit="h",
                              worktree="w", argv="[]", exit_code=exit_code,
                              stdout="", stderr="", started_at="2026",
                              ended_at="2026", duration_ms=1)

    def test_a_retracted_claim_has_its_own_bucket(self):
        _root, conn = _repo(self)
        _task(conn, "t")
        self._claim(conn, "c1", 1)
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t", claim_id="c1",
                          kind="retracted", actor="kernel", payload={},
                          created_at="2026")
        got = trend.how_claims_ended(conn)
        lint = [r for r in got if r["task"] == "t"][0]["by_kind"]["lint"]
        self.assertEqual(lint["retracted"], 1)
        self.assertEqual(lint["open"], 0,
                         "a retracted claim was counted as still owed")

    def test_a_signature_that_no_longer_covers_is_not_counted_as_signed(self):
        """`lapsed` is the bucket that did not exist: a row in
        `accepted_risk` was enough to be called signed, whether or not
        `claim_state` still returns RISK_ACCEPTED."""
        _root, conn = _repo(self)
        _task(conn, "t")
        self._claim(conn, "c2", 1)
        ledger.insert(conn, "accepted_risk", claim_id="c2", kind="unprovable",
                      who="p", why="w" * 60, was_tty=1,
                      git_record=".v4/risks/c2.json", subject_digest="{}",
                      created_at="2026", scope="task", cover_key="")
        got = trend.how_claims_ended(conn)
        lint = [r for r in got if r["task"] == "t"][0]["by_kind"]["lint"]
        self.assertEqual(lint["signed"] + lint["lapsed"], 1)
        self.assertIn("lapsed", lint)


class TheGateReadsItsOwnPayloadThroughOneShape(unittest.TestCase):
    """`gate` counts refusals, and an allowed write is not one."""

    def test_a_refusal_and_an_allowed_write_are_different(self):
        _root, conn = _repo(self)
        _task(conn, "t")
        _event(conn, "t", "hook_seen", "2026-08-01T00:01:00+00:00",
               allowed=1, basis="in-scope")
        allowed = json.dumps(trend.gate(conn), sort_keys=True, default=str)
        _event(conn, "t", "hook_seen", "2026-08-01T00:02:00+00:00",
               allowed=0, basis="protected")
        refused = json.dumps(trend.gate(conn), sort_keys=True, default=str)
        self.assertNotEqual(allowed, refused)


class ShipHoldsOnTheChain(unittest.TestCase):
    """The chain half of the ship predicate had nothing asserting it.

    Removing `chain_ok` from `ok = (not blocked) and chain_ok and not
    unconfirmed` left the whole suite green -- so the one line that stops a
    tampered ledger from shipping was covered by nothing. And `lenses_run`
    exists so that "nobody reviewed this" and "three reviewers found nothing"
    stop looking alike; nothing asserted that either.
    """

    def _shipped(self, root, conn, tamper):
        from kernel import config, lifecycle
        _task(conn, "t")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        ledger.append_attempt(conn, claim_id="c1", subject_digest="{}",
                              checker_sha="s", config_sha="c", head_commit="h",
                              worktree="w", argv="[]", exit_code=0, stdout="",
                              stderr="", started_at="2026", ended_at="2026",
                              duration_ms=1)
        # The anchor `audit_chain` asks for. Without it every chain reports a
        # problem, which would make the control below vacuous.
        row = conn.execute("SELECT id, row_hash FROM attempt "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        n = conn.execute("SELECT count(*) n FROM attempt").fetchone()["n"]
        (root / ".v4" / "chain_head.json").write_text(json.dumps(
            {"attempts": n, "last_id": row["id"], "head_hash": row["row_hash"]}))
        if tamper:
            conn.execute("DROP TRIGGER IF EXISTS no_update_attempt")
            conn.execute("UPDATE attempt SET worktree = 'somewhere else'")
            conn.commit()
        ok, rep = lifecycle.ship(conn, config.RepoConfig(root), "t")
        rep["ok"] = ok
        return rep

    def test_a_tampered_attempt_holds_the_task(self):
        root, conn = _repo(self)
        rep = self._shipped(root, conn, tamper=True)
        self.assertFalse(rep["ok"], "an edited attempt shipped")
        self.assertFalse(rep["chain_ok"])
        self.assertTrue(any("edited in place" in p
                            for p in rep["chain_problems"]),
                        rep["chain_problems"])

    def test_and_an_untouched_one_leaves_the_chain_out_of_it(self):
        """The control: holding on the chain unconditionally would pass the
        test above, and every task would be unshippable."""
        root, conn = _repo(self)
        rep = self._shipped(root, conn, tamper=False)
        self.assertTrue(rep["chain_ok"], rep["chain_problems"])

    def test_the_report_says_which_lenses_ran(self):
        root, conn = _repo(self)
        rep = self._shipped(root, conn, tamper=False)
        self.assertIn("lenses_run", rep)
        self.assertEqual(rep["lenses_run"], [],
                         "nobody reviewed this, and the report has to be able "
                         "to say so as a different thing from finding nothing")


class TheWholeReportIsOneCall(unittest.TestCase):
    """`trend.report` gathers the six readings above into one dict.

    A claim raised to verify that `review add` accepts a lens slug it does not
    know -- its own note says so -- and it names this symbol, so this is what
    closes it: the readings the repairs above changed have to survive being
    asked for together.
    """

    def test_every_reading_is_there(self):
        _root, conn = _repo(self)
        _task(conn, "t")
        got = trend.report(conn)
        for key in ("tasks", "engagement_lag", "gate", "how_claims_ended",
                    "inherited", "signatures"):
            self.assertIn(key, got, key)

    def test_and_it_carries_the_watched_flag_the_repairs_added(self):
        """The control on this file as a whole: the distinctions are only
        worth anything if they reach the report a person reads."""
        _root, conn = _repo(self)
        _task(conn, "quiet")
        got = trend.report(conn)
        self.assertFalse(got["engagement_lag"][0]["watched"])
        self.assertIn("why", got["inherited"][0])


class TheReportThatAnswersWhoSigned(unittest.TestCase):
    """`tty` was that answer until it stopped being able to be.

    Three routes reach a signature. Two of them -- `--no-tty-check` and
    `--as-monitor` -- are both `was_tty = 0`, so the only report that says who
    signed put a second session's signature in the same bucket as the worker
    signature the third value was added to be told apart from. Measured on this
    repo the day the flag landed: three `agent` signatures, all three by the
    session whose work they waived, and nothing here could have said so.
    """

    def _sign(self, conn, cid, **cols):
        ledger.insert(conn, "claim", id=cid, task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        ledger.insert(conn, "accepted_risk", claim_id=cid, kind="unprovable",
                      who="p", why="w" * 60,
                      git_record="record.json", subject_digest="{}",
                      created_at="2026", scope="task", cover_key="", **cols)

    def test_the_two_routes_that_share_was_tty_are_counted_apart(self):
        _root, conn = _repo(self)
        _task(conn, "t")
        self._sign(conn, "c-worker", was_tty=0, signed_by=risk.AGENT)
        self._sign(conn, "c-monitor", was_tty=0, signed_by=risk.MONITOR)
        got = trend.signatures(conn)
        self.assertEqual(got["tty"], {"tty": 0, "no_tty": 2},
                         "which is why this column could not answer it")
        self.assertEqual(got["by_signer"][risk.AGENT], 1)
        self.assertEqual(got["by_signer"][risk.MONITOR], 1)

    def test_a_route_nobody_took_reads_as_zero_rather_than_as_absent(self):
        """Silence and "none of these" look the same on a missing key, and the
        question a reader brings is "has anybody signed as a monitor here"."""
        _root, conn = _repo(self)
        _task(conn, "t")
        self._sign(conn, "c-worker", was_tty=0, signed_by=risk.AGENT)
        got = trend.signatures(conn)
        self.assertEqual(got["by_signer"],
                         {risk.PERSON: 0, risk.AGENT: 1, risk.MONITOR: 0})

    def test_a_row_from_before_the_column_is_its_own_bucket(self):
        """It is not inferred from `was_tty`: that would be a second copy of
        `risk.accept`'s rule, applied to rows the rule never ran on. The ledger
        refuses UPDATE, so the bucket is permanent."""
        _root, conn = _repo(self)
        _task(conn, "t")
        self._sign(conn, "c-old", was_tty=1)          # written before signed_by
        got = trend.signatures(conn)
        self.assertEqual(got["by_signer"][risk.UNRECORDED], 1)
        self.assertEqual(got["by_signer"][risk.PERSON], 0,
                         "was_tty=1 must not be read back as `person`")

    def test_it_reaches_the_whole_report(self):
        _root, conn = _repo(self)
        _task(conn, "t")
        self._sign(conn, "c-monitor", was_tty=0, signed_by=risk.MONITOR)
        self.assertIn("by_signer", trend.report(conn)["signatures"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
